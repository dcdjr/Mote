"""One offline Qt speech engine, initialized lazily when Read aloud is clicked."""

import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtTextToSpeech import QTextToSpeech


class SpeechService(QObject):
    speaking_changed = Signal(bool)
    failed = Signal(str)

    def __init__(self, parent=None, *, engine=None):
        super().__init__(parent)
        self._engine = engine
        self.speaking = False
        self._handling_error = False
        if engine is not None:
            self._connect_engine()

    def _connect_engine(self):
        self._engine.stateChanged.connect(self._state_changed)
        self._engine.errorOccurred.connect(self._error)

    def _initialize(self):
        if self._engine is None:
            # Never silently choose an arbitrary plugin or a cloud voice.
            choices = ("sapi",) if sys.platform == "win32" else ("darwin", "speechd", "flite")
            available = QTextToSpeech.availableEngines()
            name = next((name for name in choices if name in available), None)
            if name is None:
                self.failed.emit("No supported offline speech engine is available. On Windows, install a system speech voice.")
                return False
            self._engine = QTextToSpeech(name, self)
            self._connect_engine()
        if self._engine.state() == QTextToSpeech.State.Error or not self._engine.availableVoices():
            self.failed.emit(self._engine.errorString() or "No speech voices are available. Install a Windows speech voice.")
            return False
        return True

    def toggle(self, text):
        if self.speaking:
            self.stop()
            return
        if not text.strip() or not self._initialize():
            return
        self.stop()
        self._set_speaking(True)
        self._engine.say(text)

    def _set_speaking(self, value):
        if value != self.speaking:
            self.speaking = value
            self.speaking_changed.emit(value)

    def _state_changed(self, state):
        self._set_speaking(state in (QTextToSpeech.State.Speaking, QTextToSpeech.State.Synthesizing))

    def _error(self, reason, message):
        if self._handling_error:
            return
        self._handling_error = True
        try:
            self.stop()
        finally:
            self._handling_error = False
        self.failed.emit(message or "Speech playback failed. Check your Windows voice and audio output.")

    def stop(self):
        if self._engine is not None:
            self._engine.stop(QTextToSpeech.BoundaryHint.Immediate)
        self._set_speaking(False)

    def shutdown(self):
        self.stop()
        if self._engine is not None:
            self._engine.deleteLater()
            self._engine = None
