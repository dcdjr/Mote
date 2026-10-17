"""Voice lifecycle without opening hardware, downloading models, or sending prompts."""

import os
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from services.stt import VoiceCancelled, VoiceError, VoiceService, WhisperRecognizer, capture_audio, resample_audio
from ui.popup import AssistantPopup


class CaptureDevice:
    class PortAudioError(Exception):
        pass

    class CallbackStop(Exception):
        pass

    def __init__(self, blocks=1, silent=False, rate=16000, overflow=False, cancel=None):
        self.blocks, self.silent, self.rate = blocks, silent, rate
        self.overflow, self.cancel = overflow, cancel
        self.closed = False
        self.samples = 0
        self.checks = []

    def query_devices(self, **kwargs):
        return {"max_input_channels": 1, "default_samplerate": self.rate}

    def check_input_settings(self, **kwargs):
        self.checks.append(kwargs["samplerate"])
        if kwargs["samplerate"] != self.rate:
            raise self.PortAudioError("Unsupported rate")

    def InputStream(self, **kwargs):
        owner = self

        class Stream:
            active = True

            def __enter__(self):
                for _ in range(owner.blocks):
                    data = np.zeros((1000, 1), dtype=np.float32) if owner.silent else np.ones((1000, 1), dtype=np.float32) * 0.1
                    try:
                        kwargs["callback"](data, 1000, None, owner.overflow)
                        owner.samples += 1000
                    except owner.CallbackStop:
                        break
                if owner.cancel:
                    owner.cancel.set()
                return self

            def __exit__(self, *args):
                owner.closed = True

        return Stream()


class AudioTests(unittest.TestCase):
    def test_supported_rate_and_sixty_second_sample_cap(self):
        device = CaptureDevice(blocks=2000)
        finish = Event()
        audio, rate = capture_audio(Event(), finish, lambda text: None, device_module=device)
        self.assertEqual(len(audio), 60 * 16000)
        self.assertEqual(rate, 16000)
        self.assertTrue(finish.is_set())
        self.assertTrue(device.closed)

    def test_resampling_and_native_rate_fallback(self):
        device = CaptureDevice(blocks=100, rate=44100)
        audio, _ = capture_audio(Event(), Event(), lambda text: None, seconds=1, device_module=device)
        self.assertEqual(device.checks, [16000, 44100])
        self.assertEqual(len(audio), 16000)
        self.assertEqual(audio.dtype, np.float32)
        self.assertAlmostEqual(float(np.mean(audio)), 0.1, places=3)

    def test_cancel_silence_overflow_release_device(self):
        for scenario in ("cancel", "silence", "overflow"):
            with self.subTest(scenario=scenario):
                cancel = Event()
                device = CaptureDevice(blocks=20, silent=scenario == "silence",
                                       overflow=scenario == "overflow", cancel=cancel if scenario == "cancel" else None)
                with self.assertRaises(VoiceCancelled if scenario == "cancel" else VoiceError):
                    capture_audio(cancel, Event(), lambda text: None, seconds=0.25, device_module=device)
                self.assertTrue(device.closed)

    def test_microphone_permission_error_is_helpful(self):
        device = CaptureDevice()
        device.query_devices = Mock(side_effect=device.PortAudioError("Permission denied"))
        with self.assertRaisesRegex(VoiceError, "privacy permissions"):
            capture_audio(Event(), Event(), lambda text: None, device_module=device)

    def test_cached_model_reused_cpu_default_and_no_audio_upload(self):
        model = Mock()
        model.transcribe.side_effect = lambda *args, **kwargs: (iter([SimpleNamespace(text=" Recognized words ")]), None)
        recognizer = WhisperRecognizer()
        with patch("faster_whisper.WhisperModel", return_value=model) as factory:
            for _ in range(2):
                self.assertEqual(recognizer.transcribe(np.ones(16000, dtype=np.float32), Event(), lambda text: None), "Recognized words")
            factory.assert_called_once()
            self.assertTrue(factory.call_args.kwargs["local_files_only"])
            self.assertEqual(factory.call_args.kwargs["device"], "cpu")
            self.assertEqual(factory.call_args.kwargs["compute_type"], "int8")
            self.assertIsInstance(model.transcribe.call_args.args[0], np.ndarray)

    def test_first_download_notice_and_failure(self):
        statuses = []
        with patch("faster_whisper.WhisperModel", side_effect=[OSError("not cached"), OSError("offline")]):
            with self.assertRaisesRegex(VoiceError, "Could not download"):
                WhisperRecognizer().transcribe(np.ones(16000), Event(), statuses.append)
        self.assertTrue(any("Downloading base.en" in text for text in statuses))


class VoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.captured = Event()
        self.transcribing = Event()
        self.release = Event()
        self.block_transcription = False
        owner = self

        def capture(cancel, finish, status):
            owner.captured.set()
            status("Listening...")
            while not finish.wait(0.01):
                if cancel.is_set():
                    raise VoiceCancelled()
            if cancel.is_set():
                raise VoiceCancelled()
            return np.ones(16000, dtype=np.float32), 16000

        class Recognizer:
            def transcribe(self, audio, cancel, status):
                owner.transcribing.set()
                status("Transcribing...")
                if owner.block_transcription:
                    owner.release.wait(3)
                return "recognized words"  # Deliberately ignores cancellation to test stale guards.

        self.voice = VoiceService(recognizer=Recognizer(), capture=capture)
        self.motion = patch("ui.popup._motion_enabled", return_value=False)
        self.motion.start()
        self.popup = AssistantPopup(voice=self.voice, pipeline=Mock())
        self.popup.open_at_cursor()
        self.popup.follow_button.setChecked(False)

    def tearDown(self):
        self.release.set()
        self.popup.hide()
        self.voice.shutdown()
        self.popup.controller.shutdown()
        self.app.processEvents()
        self.popup.deleteLater()
        self.app.processEvents()
        self.motion.stop()

    def wait_for(self, condition):
        for _ in range(300):
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(10)
        self.fail("Timed out waiting for voice worker")

    def start(self):
        self.popup.mic_button.click()
        self.wait_for(lambda: self.voice.phase == "Listening...")

    def test_record_stop_insert_preserves_input_and_does_not_submit(self):
        self.assertFalse(self.voice.busy)
        self.assertFalse(self.captured.is_set())
        self.popup.prompt.setText("Before after")
        self.popup.prompt.setCursorPosition(7)
        self.start()
        self.assertIn("Listening", self.popup.voice_notice.text())
        self.popup.mic_button.click()
        self.wait_for(lambda: not self.voice.busy)
        self.assertEqual(self.popup.prompt.text(), "Before recognized words after")
        self.assertIsNone(self.popup._request_id)
        self.assertEqual(self.popup.voice_notice.text(), "")

    def test_unicode_cursor_and_selection_do_not_destroy_existing_text(self):
        self.popup.prompt.setText("🙂tail")
        self.popup.prompt.setCursorPosition(2)  # Qt uses UTF-16 units.
        self.popup._insert_transcript("hello")
        self.assertEqual(self.popup.prompt.text(), "🙂 hello tail")
        self.popup.prompt.setSelection(0, 2)
        before = self.popup.prompt.text()
        self.popup._insert_transcript("more")
        self.assertIn(before[1:], self.popup.prompt.text())

    def test_escape_cancels_first_then_dismisses_and_releases_worker(self):
        self.start()
        self.popup.escape.activated.emit()
        self.assertTrue(self.popup.isVisible())
        self.wait_for(lambda: not self.voice.busy)
        self.assertEqual(self.popup.prompt.text(), "")
        self.popup.escape.activated.emit()
        self.assertFalse(self.popup.isVisible())

    def test_late_transcription_after_dismissal_or_cancel_is_discarded(self):
        self.block_transcription = True
        self.start()
        self.popup.mic_button.click()
        self.wait_for(self.transcribing.is_set)
        self.popup.dismiss()
        self.popup.open_at_cursor()
        self.release.set()
        self.wait_for(lambda: not self.voice.busy)
        self.assertEqual(self.popup.prompt.text(), "")

    def test_rapid_clicks_single_worker_and_repeated_recordings(self):
        self.block_transcription = True
        self.start()
        first = self.voice._worker
        self.popup.mic_button.click()
        self.popup.mic_button.click()
        for _ in range(10):
            self.voice.toggle()
        self.assertIs(self.voice._worker, first)
        self.release.set()
        self.wait_for(lambda: not self.voice.busy)
        self.assertEqual(self.popup.prompt.text(), "")
        self.block_transcription = False
        for _ in range(2):
            self.start()
            self.popup.mic_button.click()
            self.wait_for(lambda: not self.voice.busy)
        self.assertEqual(self.popup.prompt.text(), "recognized words recognized words")

    def test_tts_stops_and_playback_is_blocked_while_capturing(self):
        with patch.object(self.popup.speech, "stop") as stop, patch.object(self.popup.speech, "toggle") as speak:
            self.start()
            stop.assert_called()
            self.popup._toggle_speech()
            speak.assert_not_called()

    def test_ui_stays_responsive_during_transcription(self):
        self.block_transcription = True
        self.start()
        self.popup.mic_button.click()
        self.wait_for(self.transcribing.is_set)
        self.popup.prompt.setText("Typed while transcribing")
        self.app.processEvents()
        self.assertEqual(self.popup.prompt.text(), "Typed while transcribing")
        self.popup.escape.activated.emit()
        self.release.set()
        self.wait_for(lambda: not self.voice.busy)
        self.assertEqual(self.popup.prompt.text(), "Typed while transcribing")

    def test_quit_waits_for_voice_without_blocking_or_repeated_quit(self):
        self.block_transcription = True
        self.start()
        self.popup.mic_button.click()
        self.wait_for(self.transcribing.is_set)
        with patch.object(self.app, "quit") as quit_app:
            self.popup._request_quit()
            quit_app.assert_not_called()
            self.assertFalse(self.popup.mic_button.isEnabled())
            self.release.set()
            self.wait_for(lambda: not self.voice.busy)
            quit_app.assert_called_once()
            self.voice.shutdown()
            quit_app.assert_called_once()

    def test_live_draft_refines_without_duplicates_and_finalizes(self):
        self.popup.prompt.setText("Before after")
        self.popup.prompt.setCursorPosition(7)
        self.popup._update_voice_draft("recognized")
        self.assertEqual(self.popup.prompt.text(), "Before recognized after")
        self.popup._update_voice_draft("recognized words")
        self.assertEqual(self.popup.prompt.text(), "Before recognized words after")
        self.popup._insert_transcript("Recognized words.")
        self.assertEqual(self.popup.prompt.text(), "Before Recognized words. after")
        self.assertIsNone(self.popup._request_id)

    def test_typing_outside_live_span_is_preserved_and_cancel_removes_only_draft(self):
        p = self.popup
        p.prompt.setText("Before after")
        p.prompt.setCursorPosition(7)
        p._update_voice_draft("draft")
        p.prompt.setCursorPosition(0)
        p.prompt.insert("Typed ")
        p._update_voice_draft("better draft")
        self.assertEqual(p.prompt.text(), "Typed Before better draft after")
        self.assertEqual(p.prompt.cursorPosition(), len("Typed "))
        p._discard_voice_draft()
        self.assertEqual(p.prompt.text(), "Typed Before after")

    def test_editing_live_words_is_not_overwritten(self):
        p = self.popup
        p._update_voice_draft("wrong words")
        p.prompt.setSelection(0, 5)
        p.prompt.insert("right")
        p._update_voice_draft("new hypothesis")
        p._insert_transcript("Final hypothesis")
        self.assertEqual(p.prompt.text(), "right words")

    def test_live_worker_updates_before_stop_and_cancels_without_late_output(self):
        preview_requested = Event()
        def live_capture(cancel, finish, status, *, preview):
            status("Listening...")
            preview(np.ones(16000, dtype=np.float32) * 0.1)
            preview_requested.set()
            while not finish.wait(0.01):
                if cancel.is_set():
                    raise VoiceCancelled()
            if cancel.is_set():
                raise VoiceCancelled()
            return np.ones(32000, dtype=np.float32) * 0.1, 16000
        self.voice.capture = live_capture
        self.start()
        self.wait_for(lambda: self.popup.prompt.text() == "recognized words")
        self.assertTrue(self.voice.recording)
        self.assertTrue(preview_requested.is_set())
        self.assertIsNone(self.popup._request_id)
        self.popup.escape.activated.emit()
        self.wait_for(lambda: not self.voice.busy)
        self.assertEqual(self.popup.prompt.text(), "")

    def test_live_worker_final_pass_replaces_draft_and_recording_closes_during_inference(self):
        preview_started, release_preview, device_closed = Event(), Event(), Event()
        class Recognizer:
            def transcribe_preview(self, audio, cancel, status):
                preview_started.set()
                release_preview.wait(3)
                return "draft words"
            def transcribe(self, audio, cancel, status):
                return "Final words."
        def capture(cancel, finish, status, *, preview):
            status("Listening...")
            preview(np.ones(16000, dtype=np.float32))
            finish.wait(3)
            device_closed.set()
            return np.ones(32000, dtype=np.float32), 16000
        self.voice.capture = capture
        self.voice.recognizer = Recognizer()
        try:
            self.start()
            self.wait_for(preview_started.is_set)
            self.popup.mic_button.click()
            self.wait_for(device_closed.is_set)
            self.assertTrue(self.voice.busy)
            release_preview.set()
            self.wait_for(lambda: not self.voice.busy)
            self.assertEqual(self.popup.prompt.text(), "Final words.")
        finally:
            release_preview.set()


if __name__ == "__main__":
    unittest.main()
