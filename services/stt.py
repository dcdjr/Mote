"""Opt-in, in-memory microphone capture and local Whisper on one bounded worker."""

import os
import inspect
import time
from pathlib import Path
from threading import Event, Lock, Thread

from PySide6.QtCore import QObject, QThread, Signal, Slot


class VoiceError(RuntimeError):
    pass


class VoiceCancelled(Exception):
    pass


class NoSpeechError(VoiceError):
    pass


def capture_audio(cancel, finish, status, *, seconds=60, device_module=None, preview=None):
    import numpy as np
    if device_module is None:
        import sounddevice as device_module
    sd = device_module
    if cancel.is_set():
        raise VoiceCancelled()
    try:
        info = sd.query_devices(kind="input")
        if info["max_input_channels"] < 1:
            raise VoiceError("No microphone is connected. Choose a default input device in Windows Sound settings.")
        rate = 16000
        try:
            sd.check_input_settings(channels=1, dtype="float32", samplerate=rate)
        except sd.PortAudioError:
            rate = int(info["default_samplerate"])
            sd.check_input_settings(channels=1, dtype="float32", samplerate=rate)
        chunks, samples, overflow = [], 0, Event()
        limit = int(rate * seconds)

        def receive(data, frames, timing, flags):
            nonlocal samples
            if cancel.is_set() or finish.is_set():
                raise sd.CallbackStop()
            if flags:
                overflow.set()
            count = min(len(data), limit - samples)
            if count:
                chunks.append(data[:count, 0].copy())
                samples += count
            if samples >= limit:
                finish.set()
                raise sd.CallbackStop()

        # Only this context owns the device; stop/cancel never open another stream.
        with sd.InputStream(channels=1, dtype="float32", samplerate=rate, callback=receive) as stream:
            status("Listening...")
            deadline = time.monotonic() + seconds
            next_preview = time.monotonic() + 1.2
            while not finish.wait(0.02):
                if cancel.is_set():
                    break
                if time.monotonic() >= deadline:
                    finish.set()
                    break
                if not stream.active:
                    raise VoiceError("Microphone stopped unexpectedly. Check its connection and permissions.")
                if preview is not None and chunks and time.monotonic() >= next_preview:
                    # Snapshot outside PortAudio's callback. The consumer keeps
                    # only the newest snapshot, never a growing inference queue.
                    preview(resample_audio(np.concatenate(tuple(chunks)), rate))
                    next_preview = time.monotonic() + 1.2
        if cancel.is_set():
            raise VoiceCancelled()
        if overflow.is_set():
            raise VoiceError("Microphone audio was interrupted. Please try a shorter recording.")
        audio = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
        if len(audio) < rate * 0.2 or not np.isfinite(audio).all() or float(np.max(np.abs(audio), initial=0)) < 0.001:
            raise NoSpeechError("No speech was heard. Check your microphone and try again.")
        return resample_audio(audio, rate), 16000
    except sd.PortAudioError as error:
        raise VoiceError("Cannot open the default microphone. Check the device and Windows microphone privacy permissions. " + str(error)) from error


def resample_audio(audio, rate):
    import numpy as np
    if rate == 16000:
        return np.ascontiguousarray(audio, dtype=np.float32)
    import av
    frame = av.AudioFrame.from_ndarray(np.asarray(audio, dtype=np.float32).reshape(1, -1), format="flt", layout="mono")
    frame.sample_rate = rate
    frame.pts = 0
    converter = av.AudioResampler(format="flt", layout="mono", rate=16000)
    frames = [*converter.resample(frame), *converter.resample(None)]
    return np.concatenate([part.to_ndarray().reshape(-1) for part in frames]).astype(np.float32)


class WhisperRecognizer:
    def __init__(self):
        self.model = None

    def transcribe(self, audio, cancel, status):
        return self._transcribe(audio, cancel, status, beam_size=3)

    def transcribe_preview(self, audio, cancel, status):
        return self._transcribe(audio, cancel, status, beam_size=1)

    def _transcribe(self, audio, cancel, status, *, beam_size):
        if cancel.is_set():
            raise VoiceCancelled()
        if self.model is None:
            from faster_whisper import WhisperModel
            name = os.getenv("MOTE_STT_MODEL", "base.en")
            if name not in ("base.en", "tiny.en"):
                raise VoiceError("MOTE_STT_MODEL must be base.en or tiny.en.")
            cache = Path(os.getenv("MOTE_STT_CACHE", str(Path.home() / ".cache" / "mote" / "whisper")))
            options = dict(device="cpu", compute_type="int8", download_root=str(cache),
                           cpu_threads=min(4, os.cpu_count() or 1))
            status("Loading voice model...")
            try:
                self.model = WhisperModel(name, local_files_only=True, **options)
            except (OSError, ValueError, RuntimeError):
                if cancel.is_set():
                    raise VoiceCancelled()
                status(f"Downloading {name} (first use)...")
                try:
                    self.model = WhisperModel(name, **options)
                except Exception as error:
                    raise VoiceError("Could not download/load the voice model. Check your connection and cache space, then try again.") from error
        if cancel.is_set():
            raise VoiceCancelled()
        status("Transcribing...")
        segments, _ = self.model.transcribe(audio, language="en", beam_size=beam_size,
                                            vad_filter=True, condition_on_previous_text=False)
        parts = []
        for segment in segments:
            if cancel.is_set():
                raise VoiceCancelled()
            parts.append(segment.text.strip())
        text = " ".join(part for part in parts if part).strip()
        if not text:
            raise NoSpeechError("No speech was recognized. Please try again.")
        return text


class VoiceWorker(QThread):
    status = Signal(int, str)
    transcript = Signal(int, str)
    draft = Signal(int, str)
    failed = Signal(int, str)

    def __init__(self, serial, recognizer, capture, parent):
        super().__init__(parent)
        self.serial, self.recognizer, self.capture = serial, recognizer, capture
        self.cancel = Event()
        self.finish_recording = Event()
        self.capture_done = Event()

    def run(self):
        ready, lock = Event(), Lock()
        latest, final, errors = [None], [None], []

        def publish(audio):
            with lock:
                latest[0] = audio
                ready.set()

        def record():
            try:
                options = {"preview": publish} if "preview" in inspect.signature(self.capture).parameters else {}
                final[0], _ = self.capture(self.cancel, self.finish_recording,
                    lambda text: self.status.emit(self.serial, text), **options)
            except Exception as error:
                errors.append(error)
            finally:
                self.capture_done.set()
                ready.set()
                if not self.cancel.is_set():
                    self.status.emit(self.serial, "Transcribing...")

        recorder = Thread(target=record, name="Mote microphone capture")
        try:
            recorder.start()
            committed = []
            chunk_size = 8 * 16000
            while not self.capture_done.is_set() and not self.cancel.is_set():
                if not ready.wait(0.05):
                    continue
                with lock:
                    audio = latest[0]
                    latest[0] = None
                    ready.clear()
                if audio is None or self.capture_done.is_set():
                    continue
                import numpy as np
                decode = getattr(self.recognizer, "transcribe_preview", self.recognizer.transcribe)

                def preview_text(part):
                    if len(part) < 8000 or float(np.max(np.abs(part), initial=0)) < 0.001:
                        return ""
                    try:
                        return decode(part, self.cancel, lambda text: self.status.emit(self.serial, text))
                    except NoSpeechError:
                        return ""

                # Decode completed chunks once. Revise only the trailing chunk;
                # work per live update stays bounded throughout a 60s recording.
                completed = len(audio) // chunk_size
                while len(committed) < completed and not self.cancel.is_set():
                    start = len(committed) * chunk_size
                    committed.append(preview_text(audio[start:start + chunk_size]))
                tail = preview_text(audio[completed * chunk_size:]) if not self.cancel.is_set() else ""
                if not self.cancel.is_set() and not self.capture_done.is_set():
                    text = " ".join(part for part in [*committed, tail] if part).strip()
                    if text:
                        self.draft.emit(self.serial, text)
                    self.status.emit(self.serial, "Listening...")
            recorder.join()
            if self.cancel.is_set():
                raise VoiceCancelled()
            if errors:
                raise errors[0]
            text = self.recognizer.transcribe(final[0], self.cancel,
                                             lambda text: self.status.emit(self.serial, text))
            if not self.cancel.is_set():
                self.transcript.emit(self.serial, text)
        except VoiceCancelled:
            pass
        except ImportError:
            self.failed.emit(self.serial, "Voice input needs faster-whisper and sounddevice. Install requirements.txt and restart Mote.")
        except VoiceError as error:
            self.failed.emit(self.serial, str(error))
        except Exception:
            self.failed.emit(self.serial, "Voice recognition failed. Check your microphone and cached model, then try again.")
        finally:
            self.finish_recording.set()
            if recorder.is_alive():
                self.cancel.set()
                recorder.join()


class VoiceService(QObject):
    status_changed = Signal(str)
    transcript = Signal(str)
    draft = Signal(str)
    cancelled = Signal()
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, parent=None, *, recognizer=None, capture=capture_audio):
        super().__init__(parent)
        self.recognizer = recognizer or WhisperRecognizer()
        self.capture = capture
        self._worker = None
        self._serial = 0
        self._active = None
        self.phase = "Idle"
        self._closing = False

    @property
    def busy(self):
        return self._worker is not None

    @property
    def active(self):
        return self._active is not None

    @property
    def recording(self):
        return (self.active and self.busy and not self._worker.finish_recording.is_set()
                and not self._worker.capture_done.is_set())

    def _set_phase(self, text):
        self.phase = text
        self.status_changed.emit(text)

    def toggle(self):
        if self._closing:
            return
        if self._worker is not None:
            if self.recording:
                self._worker.finish_recording.set()
                self._set_phase("Transcribing...")
            elif self.active:
                self.cancel()
            return
        self._serial += 1
        self._active = self._serial
        self._worker = VoiceWorker(self._serial, self.recognizer, self.capture, self)
        self._worker.status.connect(self._status)
        self._worker.transcript.connect(self._transcript)
        self._worker.draft.connect(self._draft)
        self._worker.failed.connect(self._failed)
        self._worker.finished.connect(self._retire)
        self._set_phase("Starting microphone...")
        self._worker.start()

    @Slot(int, str)
    def _status(self, serial, text):
        if serial == self._active:
            # A very fast second click must not revert Stop into Listening.
            if text == "Listening..." and not self.recording:
                return
            if self.recording and text not in ("Starting microphone...", "Listening..."):
                text = "Listening... " + text
            self._set_phase(text)

    @Slot(int, str)
    def _draft(self, serial, text):
        if serial == self._active and self.recording:
            self.draft.emit(text)

    @Slot(int, str)
    def _transcript(self, serial, text):
        if serial == self._active:
            self._active = None
            self.transcript.emit(text)

    @Slot(int, str)
    def _failed(self, serial, text):
        if serial == self._active:
            self._active = None
            self.failed.emit(text)

    @Slot()
    def _retire(self):
        self._worker.wait()
        self._worker.deleteLater()
        self._worker = None
        self._active = None
        self._set_phase("Idle")
        self.stopped.emit()

    def cancel(self):
        was_active = self.active
        self._active = None
        if was_active:
            self.cancelled.emit()
        if self._worker is not None:
            self._worker.cancel.set()
            self._worker.finish_recording.set()
            self._set_phase("Cancelling voice input...")

    def request_stop(self):
        self._closing = True
        self.cancel()
        if not self.busy:
            self.stopped.emit()

    def shutdown(self):
        self.request_stop()
        if self._worker is not None:
            self._worker.wait()
