"""Animated cursor-relative assistant, backed by the asynchronous local pipeline."""

import ctypes
import math
import sys
import time
from enum import Enum, auto

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ai.pipeline import MotePipeline
from ai.worker import PipelineController
from integration.desktop import DesktopIntegration
from services.attachments import AssistantRequest, select_attachments
from services.stt import VoiceService
from services.tts import SpeechService

GLASS_RADIUS = 28

def _motion_enabled() -> bool:
    if sys.platform == "win32":
        try:
            enabled = ctypes.c_int(1)
            query = ctypes.WinDLL("user32").SystemParametersInfoW
            query.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            query.restype = ctypes.c_int
            if query(0x1042, 0, ctypes.byref(enabled), 0):
                return bool(enabled.value)  # SPI_GETCLIENTAREAANIMATION
        except (OSError, AttributeError):
            pass
    return True


class PopupState(Enum):
    CLOSED = auto()
    OPENING = auto()
    COLLAPSED = auto()
    EXPANDING = auto()
    EXPANDED = auto()
    CLOSING = auto()


class _InputSurface(QFrame):
    """Paint a focus accent without restyling or repainting the whole popup."""

    def __init__(self, popup):
        super().__init__()
        self.popup = popup
        self.accent = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._set_accent)

    def _set_accent(self, value):
        self.accent = float(value)
        self.update()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.animation.stop()
            self.animation.setStartValue(self.accent)
            self.animation.setEndValue(1.0 if event.type() == QEvent.Type.FocusIn else 0.0)
            self.animation.setDuration(140 if self.popup.motion_enabled else 0)
            self.animation.start()
        return super().eventFilter(watched, event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.accent:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(164, 195, 228, round(65 * self.accent)), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), GLASS_RADIUS, GLASS_RADIUS)


class _SendButton(QPushButton):
    def __init__(self, popup):
        super().__init__("Send ↑")
        self.popup = popup
        self.hover = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.valueChanged.connect(self._set_hover)

    def _set_hover(self, value):
        self.hover = float(value)
        self.update()

    def event(self, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave) and hasattr(self, "animation"):
            self.animation.stop()
            self.animation.setDuration(120 if self.popup.motion_enabled else 0)
            self.animation.setStartValue(self.hover)
            self.animation.setEndValue(1.0 if event.type() == QEvent.Type.Enter else 0.0)
            self.animation.start()
        return super().event(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, round(22 * self.hover)))
        painter.drawRoundedRect(QRectF(self.rect()), 10, 10)


class _ThinkingIndicator(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.phase = 0.0
        self.text = "Routing…"
        self.opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Thinking...")
        self.setGeometry(12, 5, 350, 40)
        self.pulse = QVariantAnimation(self)
        self.pulse.setStartValue(0.0)
        self.pulse.setEndValue(2 * math.pi)
        self.pulse.setDuration(1500)
        self.pulse.setLoopCount(-1)
        self.pulse.valueChanged.connect(self._tick)
        self.hide()

    def _tick(self, value):
        self.phase = float(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(3):
            alpha = (0.48 + 0.24 * math.sin(self.phase - index * 0.8)) * self.opacity
            painter.setBrush(QColor(200, 215, 232, round(255 * alpha)))
            painter.drawEllipse(QRectF(index * 12, 17, 5, 5))
        painter.setPen(QColor(185, 194, 207, round(255 * self.opacity)))
        painter.drawText(QRect(46, 0, max(0, self.width() - 46), 40), Qt.AlignmentFlag.AlignVCenter, self.text)


class _MicrophoneButton(QPushButton):
    def __init__(self):
        super().__init__("\ue720")
        self.setFont(QFont("Segoe MDL2 Assets", 12))
        self.setFixedSize(32, 32)
        self.level = 0.0
        self.tint = (195, 130, 142)
        self.pulse = QVariantAnimation(self)
        self.pulse.setStartValue(0.0)
        self.pulse.setEndValue(2 * math.pi)
        self.pulse.setDuration(1400)
        self.pulse.setLoopCount(-1)
        self.pulse.valueChanged.connect(self._tick)

    def _tick(self, value):
        self.level = 0.5 + 0.5 * math.sin(value)
        self.update()

    def set_activity(self, active, listening, motion):
        self.tint = (195, 130, 142) if listening else (160, 196, 230)
        if active and motion:
            if self.pulse.state() != QVariantAnimation.State.Running:
                self.pulse.start()
        else:
            self.pulse.stop()
            self.level = 0.5 if active else 0.0
        self.setProperty("voiceActive", active)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.property("voiceActive"):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*self.tint, round(20 + 22 * self.level)))
            painter.drawRoundedRect(QRectF(self.rect()), 10, 10)


class _CopyButton(QPushButton):
    """Draw a small copy/check icon consistently, including in Linux preview."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("copyButton")
        self.setFixedSize(28, 28)
        self.setAccessibleName("Copy response")
        self.copied = False
        self.set_copied(False)

    def set_copied(self, copied):
        self.copied = copied
        self.setToolTip("Copied" if copied else "Copy response")
        self.setAccessibleDescription("Response copied to clipboard" if copied else "Copy the displayed response")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#a8ceb4" if self.copied else "#c5c9ce") if self.isEnabled() else QColor("#737980")
        painter.setPen(QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.copied:
            painter.drawLine(QPointF(8, 14), QPointF(12, 18))
            painter.drawLine(QPointF(12, 18), QPointF(20, 10))
        else:
            painter.drawRoundedRect(QRectF(7, 6, 10, 12), 2, 2)
            painter.drawRoundedRect(QRectF(11, 10, 10, 12), 2, 2)


class _ResponsePanel(QPlainTextEdit):
    ACTION_HEIGHT = 28

    def __init__(self):
        super().__init__()
        # A fixed row outside the scrolling viewport keeps the icon off the text
        # and scrollbar. The editor itself retains its existing glass/shadow.
        self.setViewportMargins(0, self.ACTION_HEIGHT, 0, 0)
        self.copy_button = _CopyButton(self)
        self.action_bar = None
        self.viewport().installEventFilter(self)

    def set_action_bar(self, bar):
        self.action_bar = bar
        self._position_actions()

    def _position_actions(self):
        self.copy_button.move(max(0, self.viewport().geometry().right() + 1 - self.copy_button.width()), 8)
        if self.action_bar is not None:
            # Reserve the copy button's real hit area, including when a scrollbar
            # narrows the viewport. Transparent labels still intercept clicks.
            self.action_bar.setGeometry(16, 8, max(0, self.copy_button.x() - 24), self.ACTION_HEIGHT)
        self.copy_button.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_actions()

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            self._position_actions()
        return super().eventFilter(watched, event)


class AssistantPopup(QWidget):
    WIDTH = 420
    INPUT_HEIGHT = 96
    RESPONSE_HEIGHT = 260
    PANEL_GAP = 8
    SHADOW_MARGIN = 14
    CURSOR_OFFSET = 28
    INTERACTION_MARGIN = 45
    RELEASE_MARGIN = 65
    FOLLOW_GRACE = 0.32
    FOLLOW_LAG = 0.085
    ATTACHMENT_HEIGHT = 48
    SIDE_SWITCH_MARGIN = 48
    FOLLOW_DEADBAND = 2

    def __init__(self, *, pipeline=None, speech=None, voice=None) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Mote")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont("Segoe UI", 11))
        self.preferences = None
        self.hotkey = None
        self._settings_panel = None
        self._expanded = False
        self.state = PopupState.CLOSED
        self.motion_enabled = _motion_enabled()
        self._response_height = 100
        self._input_height = self.INPUT_HEIGHT
        self.attachments = ()
        self._captured_attachments = set()
        self._attachment_notice = ""
        self._screenshot_attached = False
        self._file_dialog_open = False
        self._spoken_text = ""
        self.speech = speech or SpeechService(self)
        self.voice = voice or VoiceService(self)
        self._voice_error_message = ""
        self._voice_span = None
        self._voice_document = ""
        self._voice_detached = False
        self._updating_voice = False
        self._quitting = False
        self._quit_sent = False
        self._pipeline_stopped = False
        self.voice.status_changed.connect(self._voice_status)
        self.voice.transcript.connect(self._insert_transcript)
        self.voice.draft.connect(self._update_voice_draft)
        self.voice.cancelled.connect(self._discard_voice_draft)
        self.voice.failed.connect(self._voice_failed)
        self.voice.stopped.connect(self._maybe_quit)
        QApplication.instance().aboutToQuit.connect(self.voice.shutdown)
        self.speech.speaking_changed.connect(self._speech_state)
        self.speech.failed.connect(self._speech_failed)
        QApplication.instance().aboutToQuit.connect(self.speech.shutdown)
        self._request_id = None
        self.request_state = "Idle"
        self._stream_buffer = []
        self._stream_started = False
        self._stream_done = False
        self._rest_geometry = None
        self.follow_enabled = True
        self._follow_paused = False
        self._outside_since = None
        self._follow_position = None
        self._follow_target = None
        self._follow_goal = None
        self._placement_bounds = None
        self._placement_sides = (1, 1)
        self._follow_last_tick = time.monotonic()
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(16)
        self._follow_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._follow_timer.timeout.connect(self._follow_cursor)
        QApplication.instance().aboutToQuit.connect(self._stop_following)
        self._window_transition = QParallelAnimationGroup(self)
        self._slide = QPropertyAnimation(self, b"geometry")
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._window_transition.addAnimation(self._slide)
        self._window_transition.addAnimation(self._fade)
        self._window_transition.finished.connect(self._window_finished)
        self._expansion = QParallelAnimationGroup(self)
        self._resize_geometry = QPropertyAnimation(self, b"geometry")
        self._resize_geometry.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._resize_input = QVariantAnimation()
        self._resize_input.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._expansion.addAnimation(self._resize_geometry)
        self._expansion.addAnimation(self._resize_input)
        self._expansion.finished.connect(self._expansion_finished)
        self._reveal = QVariantAnimation(self)
        self._reveal.valueChanged.connect(self._reveal_text)
        self._thinking_fade = QVariantAnimation(self)
        self._thinking_fade.valueChanged.connect(self._fade_thinking)
        self._thinking_fade.finished.connect(self._show_stream)
        self._stream_timer = QTimer(self)
        self._stream_timer.setInterval(32)
        self._stream_timer.timeout.connect(self._flush_stream)
        self.controller = PipelineController(pipeline or MotePipeline(), self)
        self.desktop = DesktopIntegration(self)
        self.controller.status.connect(self._pipeline_status)
        self.controller.partial.connect(self._pipeline_partial)
        self.controller.completed.connect(self._pipeline_completed)
        self.controller.failed.connect(self._pipeline_failed)
        self.controller.desktop_requested.connect(self.desktop.handle)
        self.controller.attachments_captured.connect(self._attachments_captured)
        self.controller.stopped.connect(self._pipeline_shutdown_finished)
        QApplication.instance().aboutToQuit.connect(self.controller.shutdown)
        self.backdrop_status = "Translucent fallback (native backdrop not initialized)"
        self._backdrop_timer = QTimer(self)
        self._backdrop_timer.setSingleShot(True)
        self._backdrop_timer.timeout.connect(self._configure_backdrop)
        self.setStyleSheet("""
            QFrame#inputBar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(49, 53, 60, 239), stop:1 rgba(28, 31, 37, 232));
                border: 1px solid rgba(255, 255, 255, 34);
                border-top-color: rgba(255, 255, 255, 62);
                border-radius: 28px;
            }
            QLineEdit {
                background: transparent;
                border: none;
                color: #f0f0f0;
                selection-background-color: #475b70;
                placeholder-text-color: #93979c;
                padding: 0;
            }
            QPlainTextEdit#response {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(41, 45, 52, 242), stop:1 rgba(26, 29, 35, 238));
                border: 1px solid rgba(255, 255, 255, 29);
                border-top-color: rgba(255, 255, 255, 48);
                border-radius: 20px;
                padding: 16px;
                selection-background-color: #475b70;
            }
            QMenu {
                background-color: #242628;
                color: #e5e7eb;
                border: 1px solid #414448;
                padding: 4px;
            }
            QMenu::item { padding: 7px 16px; }
            QMenu::item:selected { background-color: #3a3e43; }
            QPushButton {
                background: transparent;
                color: #c5c9ce;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover { background-color: rgba(255, 255, 255, 20); }
            QPushButton:focus { border: 1px solid #8896a6; }
            QPushButton:disabled { color: #737980; }
            QPushButton#followButton:checked {
                background-color: rgba(157, 190, 226, 26);
                color: #c7d9ed;
                border: 1px solid rgba(183, 210, 242, 38);
            }
            QPushButton#speakerButton:checked {
                background-color: rgba(157, 190, 226, 26);
                color: #c7d9ed;
            }
            QScrollArea#attachments { background: transparent; border: none; }
            QWidget#attachmentContent, QWidget#attachmentViewport { background: transparent; }
            QFrame#attachmentChip {
                background: rgba(255, 255, 255, 14);
                border: 1px solid rgba(255, 255, 255, 24);
                border-radius: 12px;
            }
            QLabel { color: #c5c9ce; background: transparent; }
            QPushButton#sendButton {
                background-color: #dce4ed;
                color: #242628;
                padding: 0 12px;
            }
            QPushButton#sendButton:pressed { background-color: #b9c7d6; }
            QPushButton#copyButton {
                background-color: rgba(255, 255, 255, 6);
                border-radius: 8px;
            }
            QPushButton#copyButton:hover { background-color: rgba(255, 255, 255, 20); }
            QPushButton#copyButton:pressed { background-color: rgba(255, 255, 255, 30); }
        """)

        self._glass_stylesheet = self.styleSheet().replace("border-radius: 20px;", f"border-radius: {GLASS_RADIUS}px;").replace("border-radius: 28px;", f"border-radius: {GLASS_RADIUS}px;")
        self.setStyleSheet(self._glass_stylesheet)

        self.input_bar = _InputSurface(self)
        self.input_bar.setObjectName("inputBar")
        self.input_bar.setFixedHeight(self.INPUT_HEIGHT)
        self._resize_input.valueChanged.connect(lambda height: self.input_bar.setFixedHeight(round(height)))
        self.prompt = QLineEdit()
        self.prompt.installEventFilter(self.input_bar)
        self.prompt.setPlaceholderText("Ask Mote anything...")
        self.prompt.setAccessibleName("Ask Mote anything")
        self.prompt.setToolTip("Enter to send · Ask about your screen for automatic capture · Esc to hide · Ctrl+Q to quit")
        self.prompt.returnPressed.connect(self.submit)
        self.prompt.textChanged.connect(self._voice_input_changed)
        entry = QVBoxLayout(self.input_bar)
        entry.setContentsMargins(20, 10, 20, 12)
        entry.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.mic_button = _MicrophoneButton()
        self.mic_button.setAccessibleName("Start voice input")
        self.mic_button.setToolTip("Start voice input (local Whisper; first use downloads the model)")
        self.mic_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mic_button.clicked.connect(self._toggle_microphone)
        toolbar.addWidget(self.mic_button)
        self.attach_button = QPushButton("\ue723")  # Attach
        self.attach_button.setFont(QFont("Segoe MDL2 Assets", 12))
        self.attach_button.setFixedSize(32, 32)
        self.attach_button.setAccessibleName("Attach files")
        self.attach_button.setToolTip("Attach documents, code, or images")
        self.attach_button.clicked.connect(self._pick_attachments)
        toolbar.addWidget(self.attach_button)
        self.follow_button = QPushButton("\ue718")  # Segoe MDL2 Assets: Pin
        self.follow_button.setObjectName("followButton")
        self.follow_button.setFont(QFont("Segoe MDL2 Assets", 12))
        self.follow_button.setFixedSize(32, 32)
        self.follow_button.setAccessibleName("Follow cursor")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(True)
        self.follow_button.setToolTip("Disable cursor following")
        self.follow_button.toggled.connect(self._set_follow_enabled)
        toolbar.addWidget(self.follow_button)
        self.settings_button = QPushButton('⚙')
        self.settings_button.setFont(QFont('Segoe UI Symbol', 15))
        self.settings_button.setFixedSize(32, 32)
        self.settings_button.setAccessibleName('Mote settings')
        self.settings_button.setToolTip('Settings')
        self.settings_button.clicked.connect(self.open_settings)
        toolbar.addWidget(self.settings_button)
        self.voice_notice = QLabel()
        self.voice_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.voice_notice.setFont(QFont("Segoe UI", 9))
        self.voice_notice.setMaximumWidth(160)
        toolbar.addWidget(self.voice_notice, 1)
        self.send_button = _SendButton(self)
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedHeight(32)
        self.send_button.setAccessibleName("Send prompt")
        self.send_button.setToolTip("Send prompt (Enter)")
        self.send_button.clicked.connect(self.submit)
        toolbar.addWidget(self.send_button)
        entry.addLayout(toolbar)
        self.attachment_strip = QScrollArea()
        self.attachment_strip.setObjectName("attachments")
        self.attachment_strip.setFixedHeight(42)
        self.attachment_strip.setWidgetResizable(True)
        self.attachment_strip.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.attachment_strip.viewport().setObjectName("attachmentViewport")
        self.attachment_strip.viewport().setAutoFillBackground(False)
        chip_content = QWidget()
        chip_content.setObjectName("attachmentContent")
        self.chip_layout = QHBoxLayout(chip_content)
        self.chip_layout.setContentsMargins(0, 0, 0, 0)
        self.chip_layout.setSpacing(6)
        self.attachment_strip.setWidget(chip_content)
        # QScrollArea.setWidget() turns autoFillBackground on; undo it afterwards
        # so the chip row keeps the existing glass instead of a white rectangle.
        chip_content.setAutoFillBackground(False)
        self.attachment_strip.hide()
        entry.addWidget(self.attachment_strip)
        entry.addWidget(self.prompt)

        self.response = _ResponsePanel()
        self.response.setObjectName("response")
        self.response.setReadOnly(True)
        self.response.setAccessibleName("Mote response")
        self.response.setMinimumHeight(0)
        self.response.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        # The viewport must not paint an opaque rectangle over the glass surface.
        self.response.viewport().setAutoFillBackground(False)
        self.response.setViewportMargins(0, 28, 0, 0)
        self.response_actions = QWidget(self.response)
        actions = QHBoxLayout(self.response_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.response_notice = QLabel()
        self.response_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.response_notice.setFont(QFont("Segoe UI", 9))
        self.speaker_button = QPushButton("\ue767")  # Volume
        self.speaker_button.setObjectName("speakerButton")
        self.speaker_button.setFont(QFont("Segoe MDL2 Assets", 11))
        self.speaker_button.setFixedSize(28, 28)
        self.speaker_button.setCheckable(True)
        self.speaker_button.setEnabled(False)
        self.speaker_button.setAccessibleName("Read aloud")
        self.speaker_button.setToolTip("Read aloud")
        self.speaker_button.clicked.connect(self._toggle_speech)
        actions.addWidget(self.speaker_button)
        actions.addWidget(self.response_notice, 1)
        self.copy_notice = QLabel()
        self.copy_notice.setTextFormat(Qt.TextFormat.PlainText)
        self.copy_notice.setFont(QFont("Segoe UI", 9))
        self.copy_notice.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        actions.addWidget(self.copy_notice)
        self.response.set_action_bar(self.response_actions)
        self.copy_button = self.response.copy_button
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_response)
        self._copy_available = False
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.setInterval(2200)
        self._copy_timer.timeout.connect(self._reset_copy_feedback)
        self._clipboard_retry = QTimer(self)
        self._clipboard_retry.setSingleShot(True)
        self._clipboard_retry.setInterval(40)
        self._clipboard_retry.timeout.connect(self._write_clipboard)
        self._copy_payload = None
        self._copy_attempts = 0
        self.response.textChanged.connect(self._response_text_changed)
        self.thinking = _ThinkingIndicator(self.response.viewport())
        self._reveal_text(1.0)
        self.response.hide()

        for surface in (self.input_bar, self.response):
            shadow = QGraphicsDropShadowEffect(surface)
            shadow.setBlurRadius(20)
            shadow.setOffset(0, 3)
            shadow.setColor(QColor(0, 0, 0, 95))
            surface.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        # The window geometry drives expansion; a layout-imposed minimum would
        # otherwise jump to the response's size hint before the animation starts.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(*([self.SHADOW_MARGIN] * 4))
        layout.setSpacing(self.PANEL_GAP)
        layout.addWidget(self.input_bar)
        layout.addWidget(self.response, 1)
        self.resize(self.WIDTH + 2 * self.SHADOW_MARGIN, self.INPUT_HEIGHT + 2 * self.SHADOW_MARGIN)

        self.escape = QShortcut(QKeySequence("Escape"), self)
        self.escape.activated.connect(self._escape_pressed)
        self.quit_shortcut = QShortcut(self)
        self.quit_shortcut.setKey(QKeySequence("Ctrl+Q"))
        self.quit_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.quit_shortcut.activated.connect(self._request_quit)

        # Add window actions to the editors' standard right-click menus.
        for widget in (self, self.input_bar, self.prompt, self.response):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda position, target=widget: self._show_menu(target, position)
            )

    def configure_preferences(self, preferences, hotkey=None):
        self.preferences, self.hotkey = preferences, hotkey
        self.follow_button.setChecked(preferences.follow)
        self.set_glass_opacity(preferences.opacity)

    def set_glass_opacity(self, percent):
        percent = max(60, min(100, int(percent)))
        self.glass_opacity = percent
        style = self._glass_stylesheet
        for rgb, alpha in (('49, 53, 60',239), ('28, 31, 37',232), ('41, 45, 52',242), ('26, 29, 35',238)):
            style = style.replace(f'rgba({rgb}, {alpha})', f'rgba({rgb}, {round(alpha * percent / 100)})')
        self.setStyleSheet(style)

    def open_settings(self):
        if self._settings_panel is None:
            from config import Preferences
            from ui.settings import SettingsPanel
            self._settings_panel = SettingsPanel(self, self.preferences or Preferences(), self.hotkey)
        self._pause_following()
        self._settings_panel.open_near()

    def event(self, event) -> bool:
        result = super().event(event)
        # Defer until Qt has finished creating/applying the native window styles.
        if event.type() in (QEvent.Type.WinIdChange, QEvent.Type.Show) and hasattr(self, "_backdrop_timer"):
            self._backdrop_timer.start(0)
        return result

    def _speech_state(self, speaking):
        self.speaker_button.setChecked(speaking)
        action = "Stop reading" if speaking else "Read aloud"
        self.speaker_button.setToolTip(action)
        self.speaker_button.setAccessibleName(action)

    def _toggle_speech(self):
        if self.voice.busy:
            return
        self.speech.toggle(self._spoken_text)
        # A checkable button toggles before clicked; failed engine startup must
        # not leave it highlighted as though playback had started.
        self.speaker_button.setChecked(self.speech.speaking)

    def _toggle_microphone(self):
        if self._quitting:
            return
        self._pause_following()
        if not self.voice.busy:
            self.speech.stop()
            self._voice_error_message = ""
            self._voice_span = None
            self._voice_detached = False
        self.voice.toggle()

    def _voice_status(self, text):
        listening = self.voice.recording
        active = self.voice.active
        self.mic_button.set_activity(active, listening, self.motion_enabled)
        self.mic_button.setEnabled(not self._quitting and (active or not self.voice.busy))
        tip = ("Stop recording and finalize transcript" if listening else "Cancel voice input") if active else "Start voice input"
        self.mic_button.setToolTip(tip)
        self.mic_button.setAccessibleName(tip)
        self.voice_notice.setText(self.voice_notice.fontMetrics().elidedText(
            self._voice_error_message or ("" if text == "Idle" else text), Qt.TextElideMode.ElideRight, 155,
        ))
        self.voice_notice.setToolTip(self._voice_error_message or text)
        self.speaker_button.setEnabled(bool(self._spoken_text) and not self.voice.busy and not self._quitting)

    def _voice_failed(self, message):
        self._voice_span = None  # Preserve any usable draft on a recognition error.
        self._voice_error_message = message
        self.voice_notice.setText("Voice input unavailable")
        self.voice_notice.setToolTip(message)

    def _insert_transcript(self, text):
        self._update_voice_draft(text)
        self._voice_span = None
        if self.isVisible() and self.state != PopupState.CLOSING and not self._quitting:
            self._focus_prompt()

    def _update_voice_draft(self, text):
        if not self.isVisible() or self.state == PopupState.CLOSING or self._quitting:
            return
        text = text.strip()
        if not text or self._voice_detached:
            return
        original = self.prompt.text()
        caret = len(original.encode("utf-16-le")[:self.prompt.cursorPosition() * 2].decode("utf-16-le"))
        start, end = self._voice_span if self._voice_span is not None else (caret, caret)
        prefix = " " if start and not original[start - 1].isspace() else ""
        suffix = " " if end < len(original) and not original[end].isspace() else ""
        replacement = prefix + text + suffix
        qt_start = len(original[:start].encode("utf-16-le")) // 2
        qt_length = len(original[start:end].encode("utf-16-le")) // 2
        self._updating_voice = True
        try:
            self.prompt.setSelection(qt_start, qt_length)
            self.prompt.insert(replacement)
            # Keep a caret used to edit elsewhere from jumping to the voice text.
            if caret < start or caret > end:
                adjusted = caret + (len(replacement) - (end - start) if caret > end else 0)
                current = self.prompt.text()
                self.prompt.setCursorPosition(len(current[:adjusted].encode("utf-16-le")) // 2)
        finally:
            self._updating_voice = False
        self._voice_span = (start, start + len(replacement))
        self._voice_document = self.prompt.text()

    def _voice_input_changed(self, current):
        if self._updating_voice or self._voice_span is None:
            return
        before = self._voice_document
        left = 0
        while left < min(len(before), len(current)) and before[left] == current[left]:
            left += 1
        right = 0
        while right < min(len(before), len(current)) - left and before[-1 - right] == current[-1 - right]:
            right += 1
        changed_end = len(before) - right
        start, end = self._voice_span
        if changed_end <= start:
            delta = len(current) - len(before)
            self._voice_span = (start + delta, end + delta)
        elif left < end:
            # Respect edits inside the provisional transcript. Stop replacing
            # that span; the user can finish editing and submit it themselves.
            self._voice_span = None
            self._voice_detached = True
        self._voice_document = current

    def _discard_voice_draft(self):
        if self._voice_span is not None:
            start, end = self._voice_span
            text = self.prompt.text()
            self._updating_voice = True
            try:
                self.prompt.setSelection(len(text[:start].encode("utf-16-le")) // 2,
                                         len(text[start:end].encode("utf-16-le")) // 2)
                self.prompt.insert("")
            finally:
                self._updating_voice = False
        self._voice_span = None

    def _escape_pressed(self):
        if self.voice.active:
            self.voice.cancel()
        else:
            self.dismiss()

    def _pipeline_shutdown_finished(self):
        self._pipeline_stopped = True
        self._maybe_quit()

    def _maybe_quit(self):
        if (self._quitting and self._pipeline_stopped and not self.voice.busy and not self._quit_sent
                and not (self._settings_panel is not None and self._settings_panel.busy)):
            self._quit_sent = True
            QApplication.instance().quit()

    def _speech_failed(self, message):
        self.response_notice.setText("Speech unavailable")
        self.response_notice.setToolTip(message)
        self.speaker_button.setToolTip(message)

    def _pick_attachments(self):
        self._file_dialog_open = True
        self._pause_following()
        try:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Attach files", "",
                "Supported files (*.txt *.md *.log *.py *.js *.ts *.html *.css *.json *.csv *.pdf *.png *.jpg *.jpeg *.webp);;All files (*)",
            )
            if paths:
                self.attach_files(paths)
        finally:
            self._file_dialog_open = False
            self._pause_following()  # Preserve the usual leave-zone grace period.
            if self.isVisible():
                self._focus_prompt()

    def attach_files(self, paths):
        self.attachments, errors = select_attachments(paths, self.attachments)
        self._refresh_attachments()
        if errors:
            message = QMessageBox(self)
            message.setWindowTitle("Could not attach files")
            message.setIcon(QMessageBox.Icon.Warning)
            message.setTextFormat(Qt.TextFormat.PlainText)
            message.setText("\n".join(errors[:6]))
            message.setDetailedText("\n".join(errors))
            message.exec()

    def _remove_attachment(self, attachment_id):
        self.attachments = tuple(a for a in self.attachments if a.id != attachment_id)
        self._refresh_attachments()

    def _refresh_attachments(self):
        while self.chip_layout.count():
            item = self.chip_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for attachment in self.attachments:
            chip = QFrame()
            chip.setObjectName("attachmentChip")
            chip.setFixedHeight(28)
            chip.setToolTip(attachment.name)
            row = QHBoxLayout(chip)
            row.setContentsMargins(8, 0, 3, 0)
            row.setSpacing(4)
            icon = QLabel("\ue8a5")
            icon.setFont(QFont("Segoe MDL2 Assets", 10))
            row.addWidget(icon)
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setFont(QFont("Segoe UI", 9))
            label.setText(label.fontMetrics().elidedText(attachment.name, Qt.TextElideMode.ElideMiddle, 110))
            label.setFixedWidth(110)
            row.addWidget(label)
            remove = QPushButton("×")
            remove.setFixedSize(22, 22)
            remove.setAccessibleName(f"Remove {attachment.name}")
            remove.setToolTip(f"Remove {attachment.name}")
            remove.clicked.connect(lambda checked=False, item_id=attachment.id: self._remove_attachment(item_id))
            row.addWidget(remove)
            self.chip_layout.addWidget(chip)
        self.chip_layout.addStretch()
        self.attachment_strip.setVisible(bool(self.attachments))
        self._input_height = self.INPUT_HEIGHT + (self.ATTACHMENT_HEIGHT if self.attachments else 0)
        if self.isVisible() and self.state != PopupState.CLOSING:
            self._animate_layout()
        else:
            self.input_bar.setFixedHeight(self._input_height)

    def _attachments_captured(self, request_id, info):
        if request_id == self._request_id:
            self._captured_attachments = set(info["ids"])
            if info["truncated"]:
                self._attachment_notice = "File excerpts used"
                self.response_notice.setText(self._attachment_notice)
                self.response_notice.setToolTip(
                    "Content was truncated to fit the file/context limits: " + ", ".join(info["truncated"])
                )

    def _configure_backdrop(self) -> None:
        """Request documented DWM Acrylic only on a compatible native surface.

        Qt's Windows raster translucency normally uses WS_EX_LAYERED. Do not
        remove that style or toggle WA_TranslucentBackground after creation:
        doing so can break alpha corners, shadows, and mouse input. DWM paints
        a whole-window backdrop, so our separate panels must retain the fallback
        on that configuration. A successful HRESULT alone is not proof of blur.
        """
        self.backdrop_status = "Translucent fallback (platform has no supported DWM backdrop)"
        if sys.platform != "win32" or QApplication.platformName() != "windows":
            return
        if sys.getwindowsversion().build < 22621:
            return
        try:
            from ctypes import wintypes

            hwnd = wintypes.HWND(int(self.winId()))
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_style = user32.GetWindowLongW
            get_style.argtypes = [wintypes.HWND, ctypes.c_int]
            get_style.restype = wintypes.LONG
            if get_style(hwnd, -20) & 0x00080000:  # GWL_EXSTYLE / WS_EX_LAYERED
                self.backdrop_status = "Translucent fallback (Qt per-pixel layered window)"
                return
            dwm = ctypes.WinDLL("dwmapi")
            set_attribute = dwm.DwmSetWindowAttribute
            set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            set_attribute.restype = ctypes.c_long
            composed = wintypes.BOOL()
            dwm.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
            dwm.DwmIsCompositionEnabled.restype = ctypes.c_long
            if dwm.DwmIsCompositionEnabled(ctypes.byref(composed)) < 0 or not composed.value:
                return
            material = ctypes.c_int(3)  # DWMSBT_TRANSIENTWINDOW (Desktop Acrylic)
            result = set_attribute(hwnd, 38, ctypes.byref(material), ctypes.sizeof(material))
            if result < 0:
                self.backdrop_status = f"Translucent fallback (DWM HRESULT {result:#x})"
                return
            # This UI requires transparent corners, a panel gap and shadow gutters.
            # A whole-HWND backdrop would fill those pixels. Until a compatible
            # compositor surface is available, undo the probe and preserve alpha.
            material.value = 1  # DWMSBT_NONE
            reset = set_attribute(hwnd, 38, ctypes.byref(material), ctypes.sizeof(material))
            self.backdrop_status = (
                "Translucent fallback (whole-window Acrylic conflicts with panel alpha)"
                if reset >= 0 else "DWM backdrop reset failed; native appearance needs inspection"
            )
        except (OSError, AttributeError, ValueError) as error:
            self.backdrop_status = f"Translucent fallback ({type(error).__name__})"

    def _show_menu(self, target, position) -> None:
        menu = target.createStandardContextMenu() if target in (self.prompt, self.response) else QMenu(self)
        if menu.actions():
            menu.addSeparator()
        menu.addAction("Hide Mote\tEsc", self.dismiss)
        menu.addAction("Quit Mote\tCtrl+Q", self._request_quit)
        self._pause_following()
        menu.exec(target.mapToGlobal(position))
        menu.deleteLater()

    def _fit_to_screen(self, screen) -> None:
        self.input_bar.setFixedHeight(self._input_height)
        bounds = screen.availableGeometry()
        height = self._input_height
        if self._expanded:
            height += self.PANEL_GAP + self._response_height
        margin = 2 * self.SHADOW_MARGIN
        self.resize(min(self.WIDTH + margin, bounds.width()), min(height + margin, bounds.height()))

    def _clamp_to_screen(self, screen) -> None:
        bounds = screen.availableGeometry()
        self.move(
            max(bounds.left(), min(self.x(), bounds.right() - self.width() + 1)),
            max(bounds.top(), min(self.y(), bounds.bottom() - self.height() + 1)),
        )

    def toggle_at_cursor(self) -> None:
        if self._file_dialog_open:
            return
        if self.isVisible() and self.state != PopupState.CLOSING:
            self.dismiss()
        else:
            self.open_at_cursor()

    def open_at_cursor(self) -> None:
        self.motion_enabled = _motion_enabled()
        if self.isVisible() and self.state != PopupState.CLOSING:
            # The follower retargets smoothly. Never replay entrance motion or
            # move a pinned window merely because the hotkey was pressed again.
            self._focus_prompt()
            return
        self.desktop.remember_target()
        reopening = self.isVisible()
        self._window_transition.stop()
        self._expansion.stop()
        if reopening:
            if not self.response.toPlainText() and self._request_id is None:
                self._expanded = False
                self.response.hide()
            self._position_at_cursor()
            target = self.geometry()
        else:
            self._position_at_cursor()
            target = self.geometry()
            start = QRect(target)
            start.translate(0, 8 if self.motion_enabled else 0)
            start = self._bounded_rect(start)
            self.setGeometry(start)
            self.setWindowOpacity(0.0 if self.motion_enabled else 1.0)
        self._rest_geometry = QRect(target)
        self.state = PopupState.OPENING
        self.show()
        self._focus_prompt()
        self._animate_window(target, 1.0, 180, QEasingCurve.Type.OutCubic)
        self._start_following()

    def _position_at_cursor(self, resize=True) -> None:
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        if resize:
            self._fit_to_screen(screen)
        else:
            bounds = screen.availableGeometry()
            self.resize(min(self.width(), bounds.width()), min(self.height(), bounds.height()))
        bounds = screen.availableGeometry()
        destination = self._cursor_destination(cursor, bounds, self.width(), self.height(), reset=True)
        self.move(destination.toPoint())
        self._clamp_to_screen(screen)

    def _cursor_destination(self, cursor, bounds, width, height, *, reset=False):
        """Keep each side until the opposite side offers meaningfully more room.

        All values are Qt logical coordinates; physical DPI scaling belongs to
        Qt. Re-evaluate placement once on a new monitor/work-area geometry.
        """
        reset = reset or bounds != self._placement_bounds
        coordinates, sides = [], []
        for value, low, high, size, side in (
            (cursor.x(), bounds.left(), bounds.right() + 1, width, self._placement_sides[0]),
            (cursor.y(), bounds.top(), bounds.bottom() + 1, height, self._placement_sides[1]),
        ):
            before = value - low - self.CURSOR_OFFSET
            after = high - value - self.CURSOR_OFFSET
            if reset:
                side = 1 if after >= size else -1
            else:
                room, opposite = (after, before) if side == 1 else (before, after)
                if room < size - self.SIDE_SWITCH_MARGIN and opposite > room + self.SIDE_SWITCH_MARGIN:
                    side = -side
            position = value + self.CURSOR_OFFSET if side == 1 else value - self.CURSOR_OFFSET - size
            coordinates.append(max(low, min(position, high - size)))
            sides.append(side)
        if reset:
            self._follow_goal = None
        self._placement_bounds = QRect(bounds)
        self._placement_sides = tuple(sides)
        return QPointF(*coordinates)

    def _set_follow_enabled(self, enabled: bool) -> None:
        self.follow_enabled = enabled
        if self.preferences is not None:
            try:
                self.preferences.set('follow', enabled)
            except OSError as error:
                self.follow_button.setToolTip(str(error))
        self.follow_button.setToolTip("Disable cursor following" if enabled else "Enable cursor following")
        self.follow_button.setAccessibleDescription("Enabled" if enabled else "Disabled")
        if enabled:
            self._start_following()
        else:
            self._stop_following()

    def _start_following(self) -> None:
        self._reset_follow_motion()
        self._outside_since = None
        self._follow_paused = False
        self._follow_last_tick = time.monotonic()
        if self.follow_enabled and self.isVisible() and self.state != PopupState.CLOSING:
            self._follow_timer.start()

    def _stop_following(self) -> None:
        self._follow_timer.stop()
        self._reset_follow_motion()
        self._outside_since = None

    def _reset_follow_motion(self) -> None:
        self._follow_position = self._follow_target = self._follow_goal = None

    def _pause_following(self) -> None:
        self._follow_paused = True
        self._outside_since = None
        self._reset_follow_motion()

    def _follow_cursor(self) -> None:
        if not self.isVisible() or not self.follow_enabled or self.state == PopupState.CLOSING:
            self._stop_following()
            return
        if self.desktop.busy or self._file_dialog_open or (self._settings_panel is not None and self._settings_panel.isVisible()):
            self._reset_follow_motion()
            self._follow_last_tick = time.monotonic()
            return
        now = time.monotonic()
        elapsed = min(0.05, max(0.0, now - self._follow_last_tick))
        self._follow_last_tick = now
        cursor = QCursor.pos()
        # Ignore the transparent shadow gutters when measuring interaction.
        margin = self.SHADOW_MARGIN
        surface = self.geometry().adjusted(margin, margin, -margin, -margin)
        near = surface.adjusted(-self.INTERACTION_MARGIN, -self.INTERACTION_MARGIN,
                                self.INTERACTION_MARGIN, self.INTERACTION_MARGIN)
        release = surface.adjusted(-self.RELEASE_MARGIN, -self.RELEASE_MARGIN,
                                   self.RELEASE_MARGIN, self.RELEASE_MARGIN)
        if (near.contains(cursor) or QApplication.mouseButtons() != Qt.MouseButton.NoButton
                or QApplication.activePopupWidget() is not None):
            self._pause_following()
            return
        if self._follow_paused:
            if release.contains(cursor):
                self._outside_since = None
                return
            if self._outside_since is None:
                self._outside_since = now
            if now - self._outside_since < self.FOLLOW_GRACE:
                return
            self._follow_paused = False
        # Geometry animations have exclusive ownership until they finish. We
        # still sample interaction above; afterwards we ease from the final rect.
        if self.state in (PopupState.OPENING, PopupState.EXPANDING):
            self._reset_follow_motion()
            return
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        width = min(self.WIDTH + 2 * self.SHADOW_MARGIN, bounds.width())
        natural_height = self._input_height + 2 * self.SHADOW_MARGIN
        if self._expanded:
            natural_height += self.PANEL_GAP + self._response_height
        height = min(natural_height, bounds.height())
        target = self._cursor_destination(cursor, bounds, width, height)
        if self._follow_goal is None or (target - self._follow_goal).manhattanLength() > self.FOLLOW_DEADBAND:
            self._follow_goal = target
        target = self._follow_goal
        if self._follow_position is None:
            self._follow_position = QPointF(self.pos())
            self._follow_target = QPointF(self.pos())
        if self.motion_enabled:
            # Two exponential filters give a gentle start and stop instead of
            # an immediate velocity jump. This is their exact time-based step,
            # so different timer/frame rates produce the same trajectory.
            step = elapsed / self.FOLLOW_LAG
            decay = math.exp(-step)
            offset = self._follow_target - target
            position = target + (self._follow_position - target + offset * step) * decay
            self._follow_target = target + offset * decay
        else:
            position = target
            self._follow_target = target
        if ((target - position).manhattanLength() < 0.25
                and (target - self._follow_target).manhattanLength() < 0.25):
            position = self._follow_target = target
        self._follow_position = position
        rect = QRect(round(position.x()), round(position.y()), width, height)
        if not any(s.availableGeometry().intersects(rect) for s in QApplication.screens()):
            # Disjoint/mixed-DPI desktops may have logical gaps. Do not let the
            # trailing window disappear into one; rebase on the destination.
            rect.moveLeft(max(bounds.left(), min(rect.x(), bounds.right() - width + 1)))
            rect.moveTop(max(bounds.top(), min(rect.y(), bounds.bottom() - height + 1)))
            self._follow_position = self._follow_target = QPointF(rect.topLeft())
        if rect != self.geometry():
            # Intermediate frames may span adjacent monitors; only the target is
            # clamped, avoiding a teleport when the pointer crosses a boundary.
            self.setGeometry(rect)
        self._rest_geometry = QRect(rect)

    def _focus_prompt(self) -> None:
        self.raise_()
        self.activateWindow()
        self.prompt.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _animate_window(self, target, opacity, duration, easing) -> None:
        for animation, start, end in (
            (self._slide, self.geometry(), target),
            (self._fade, self.windowOpacity(), opacity),
        ):
            animation.setStartValue(start)
            animation.setEndValue(end)
            animation.setDuration(duration if self.motion_enabled else 0)
            animation.setEasingCurve(easing)
        self._window_transition.start()

    def _window_finished(self) -> None:
        if self.state == PopupState.CLOSING:
            self.hide()
        elif self.state == PopupState.OPENING:
            self.state = PopupState.EXPANDED if self._expanded else PopupState.COLLAPSED
            self._focus_prompt()

    def dismiss(self) -> None:
        if not self.isVisible() or self.state == PopupState.CLOSING:
            return
        self.voice.cancel()
        self._stop_following()
        self._cancel_response()
        self._window_transition.stop()
        self._expansion.stop()
        self.state = PopupState.CLOSING
        target = self.geometry().translated(0, 8 if self.motion_enabled else 0)
        self._animate_window(self._bounded_rect(target), 0.0, 120, QEasingCurve.Type.InCubic)

    def hideEvent(self, event) -> None:
        # Also clean up if a caller hides the widget without using dismiss().
        if hasattr(self, "_window_transition"):
            self.voice.cancel()
            self.mic_button.set_activity(False, False, False)
            self._stop_following()
            self._window_transition.stop()
            self._expansion.stop()
            self._cancel_response()
            self.state = PopupState.CLOSED
            self.setWindowOpacity(1.0)
            if not self.response.toPlainText():
                self._expanded = False
                self.response.hide()
        super().hideEvent(event)

    def _cancel_response(self) -> None:
        self.speech.stop()
        self._captured_attachments.clear()
        self.controller.cancel()
        self.desktop.cancel_pending()
        self._request_id = None
        self.request_state = "Idle"
        self._stream_timer.stop()
        self._thinking_fade.stop()
        self._reveal.stop()
        self.thinking.pulse.stop()
        self.thinking.hide()
        self._stream_buffer.clear()
        self._stream_started = False
        self._stream_done = False
        self._reveal_text(1.0)
        self._reset_copy_feedback()

    def _reset_copy_feedback(self) -> None:
        self._copy_timer.stop()
        self._clipboard_retry.stop()
        self._copy_payload = None
        self.copy_button.set_copied(False)
        self.copy_notice.clear()

    def _response_text_changed(self) -> None:
        self._reset_copy_feedback()
        self.copy_button.setEnabled(self._copy_available and bool(self.response.toPlainText().strip()))

    def _copy_response(self) -> None:
        if not self.copy_button.isEnabled():
            return
        text = self.response.toPlainText()
        if text.strip():
            self._reset_copy_feedback()
            self._copy_payload = text
            self._copy_attempts = 0
            self._write_clipboard()

    def _write_clipboard(self) -> None:
        text = self._copy_payload
        if text is None or text != self.response.toPlainText():
            return
        self._copy_attempts += 1
        try:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            copied = clipboard.text() == text
        except (RuntimeError, OSError):
            copied = False
        if copied:
            self.copy_button.set_copied(True)
            self.copy_notice.setText("Copied")
            self.copy_notice.setStyleSheet("color: #a8ceb4;")
            self.copy_notice.setToolTip("Response copied to clipboard")
            self._copy_payload = None
            self._copy_timer.start()
        elif self._copy_attempts < 3:
            self._clipboard_retry.start()
        else:
            self.copy_notice.setText("Copy failed")
            self.copy_notice.setStyleSheet("color: #dbb788;")
            self.copy_notice.setToolTip("The clipboard is busy or unavailable. Click Copy to try again.")
            self._copy_payload = None
            self._copy_timer.start()

    def _request_quit(self) -> None:
        self._quitting = True
        if self._settings_panel is not None:
            self._settings_panel.request_stop()
        self.mic_button.setEnabled(False)
        self.voice.request_stop()
        self._cancel_response()
        self._copy_available = False
        self.prompt.setEnabled(False)
        self.send_button.setEnabled(False)
        self.attach_button.setEnabled(False)
        self.response.setPlainText("Closing Mote… Waiting for the current connection to finish.")
        self.controller.request_stop()

    def closeEvent(self, event) -> None:
        self.dismiss()
        # QApplication.quit also sends close events. Accept them so a focused
        # Ctrl+Q can exit; quitOnLastWindowClosed=False still lets close() hide.
        event.accept()

    def _bounded_rect(self, rect):
        screen = (QApplication.screenAt(QCursor.pos()) if self.follow_enabled else self.screen())
        bounds = (screen or QApplication.primaryScreen()).availableGeometry()
        rect = QRect(rect)
        rect.setWidth(min(rect.width(), bounds.width()))
        rect.setHeight(min(rect.height(), bounds.height()))
        rect.moveLeft(max(bounds.left(), min(rect.x(), bounds.right() - rect.width() + 1)))
        rect.moveTop(max(bounds.top(), min(rect.y(), bounds.bottom() - rect.height() + 1)))
        return rect

    def _target_geometry(self, response_height):
        height = self._input_height + 2 * self.SHADOW_MARGIN
        if response_height is not None:
            height += self.PANEL_GAP + response_height
        target = QRect(self.geometry())
        target.setHeight(height)
        return self._bounded_rect(target)

    def _expand_to(self, height) -> None:
        self._response_height = height
        self._expanded = True
        self.response.show()
        self._animate_layout()

    def _animate_layout(self) -> None:
        self._reset_follow_motion()
        self._window_transition.stop()
        self.setWindowOpacity(1.0)
        self._expansion.stop()
        self.state = PopupState.EXPANDING
        self._resize_geometry.setStartValue(self.geometry())
        self._rest_geometry = self._target_geometry(self._response_height if self._expanded else None)
        self._resize_geometry.setEndValue(self._rest_geometry)
        self._resize_input.setStartValue(self.input_bar.height())
        self._resize_input.setEndValue(self._input_height)
        for animation in (self._resize_geometry, self._resize_input):
            animation.setDuration(300 if self.motion_enabled else 0)
        self._expansion.start()

    def _expansion_finished(self) -> None:
        if self.state == PopupState.EXPANDING:
            self.state = PopupState.EXPANDED if self._expanded else PopupState.COLLAPSED
            if self._stream_buffer or self._stream_done:
                self._start_stream_reveal()

    def _pipeline_status(self, request_id, text) -> None:
        if request_id != self._request_id:
            return
        if text == "Screenshot attached":
            self._screenshot_attached = True
            self.response_notice.setText(" · ".join(filter(None, (self._attachment_notice, "Screenshot attached"))))
            self.response_notice.setToolTip("A fresh screenshot was captured and attached to this request. Mote is excluded from capture.")
        self.request_state = text.split("…")[0].split(" ·")[0]
        self.send_button.setToolTip(text + " · Enter sends a new request")
        self.thinking.text = text
        self.thinking.setAccessibleName(text)
        self.thinking.update()

    def _pipeline_partial(self, request_id, text) -> None:
        if request_id != self._request_id:
            return
        self._stream_buffer.append(text)
        if not self._stream_timer.isActive():
            self._stream_timer.start()
        if self.state != PopupState.EXPANDING:
            self._start_stream_reveal()

    def _start_stream_reveal(self) -> None:
        if self._stream_started:
            return
        self._stream_started = True
        self.thinking.pulse.stop()
        self._thinking_fade.setStartValue(1.0)
        self._thinking_fade.setEndValue(0.0)
        self._thinking_fade.setDuration(90 if self.motion_enabled else 0)
        self._thinking_fade.start()

    def _fade_thinking(self, value) -> None:
        self.thinking.opacity = float(value)
        self.thinking.update()

    def _show_stream(self) -> None:
        if self._request_id is None:
            return
        self.thinking.hide()
        self._reveal_text(0.0)
        self._flush_stream()
        self._reveal.setStartValue(0.0)
        self._reveal.setEndValue(1.0)
        self._reveal.setDuration(200 if self.motion_enabled else 0)
        self._reveal.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reveal.start()

    def _flush_stream(self) -> None:
        if not self._stream_started or self.thinking.isVisible():
            return
        if self._stream_buffer:
            scrollbar = self.response.verticalScrollBar()
            at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
            cursor = QTextCursor(self.response.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText("".join(self._stream_buffer))
            self._stream_buffer.clear()
            if at_bottom:
                scrollbar.setValue(scrollbar.maximum())
        if self._stream_done:
            self._stream_timer.stop()
            self._copy_available = True
            self.copy_button.setEnabled(bool(self.response.toPlainText().strip()))
            self._finish_response_size()

    def _finish_response_size(self) -> None:
        document = QTextDocument()
        document.setDefaultFont(self.response.font())
        document.setPlainText(self.response.toPlainText())
        document.setTextWidth(max(80, self.response.width() - 52))
        height = min(self.RESPONSE_HEIGHT, max(100, int(document.size().height()) + 44 + self.response.ACTION_HEIGHT))
        if height != self._response_height:
            self._expand_to(height)

    def _pipeline_completed(self, request_id, text) -> None:
        if request_id != self._request_id:
            return
        self._spoken_text = text
        self.speaker_button.setEnabled(bool(text.strip()) and not self.voice.busy)
        if not self._attachment_notice and not self._screenshot_attached:
            self.response_notice.clear()
        # Commands have a single result rather than a model stream.
        if not self._stream_started and not self._stream_buffer:
            self._stream_buffer.append(text)
        self._stream_done = True
        self.request_state = "Idle"
        self.send_button.setToolTip("Send prompt (Enter)")
        if self.state != PopupState.EXPANDING:
            self._start_stream_reveal()
        if self._stream_started and not self.thinking.isVisible():
            self._flush_stream()
        if self._captured_attachments:
            self.attachments = tuple(a for a in self.attachments if a.id not in self._captured_attachments)
            self._captured_attachments.clear()
            self._refresh_attachments()

    def _pipeline_failed(self, request_id, text) -> None:
        if request_id != self._request_id:
            return
        self._spoken_text = ""
        self.speaker_button.setEnabled(False)
        self._thinking_fade.stop()
        self.thinking.pulse.stop()
        self.thinking.hide()
        self._stream_timer.stop()
        self._stream_buffer.clear()
        self._stream_started = True
        self._stream_done = True
        self._reveal.stop()
        self._reveal_text(1.0)
        self._copy_available = False
        self.response.setPlainText(text)
        self.request_state = "Error"
        self.send_button.setToolTip("Try again (Enter)")
        if self.state != PopupState.EXPANDING:
            self._finish_response_size()

    def _reveal_text(self, value) -> None:
        # Animate text alpha only, leaving the glass and its shadow intact.
        palette = self.response.palette()
        palette.setColor(QPalette.ColorRole.Text, QColor(217, 220, 223, round(255 * float(value))))
        self.response.setPalette(palette)

    def submit(self) -> None:
        text = self.prompt.text().strip()
        if not text or self.state in (PopupState.CLOSED, PopupState.CLOSING):
            return
        # Enter explicitly accepts the current draft; cancellation must not
        # remove the text the user has just chosen to send.
        self._voice_span = None
        self.voice.cancel()
        self._cancel_response()
        self._spoken_text = ""
        self.speaker_button.setEnabled(False)
        self._attachment_notice = ""
        self._screenshot_attached = False
        self.response_notice.clear()
        self._copy_available = False
        self.response.clear()
        self.request_state = "Routing"
        self.thinking.text = "Routing…"
        self.thinking.opacity = 1.0
        self.thinking.show()
        if self.motion_enabled:
            self.thinking.pulse.start()
        # Reserve a response panel once; token updates only append text.
        self._expand_to(self.RESPONSE_HEIGHT)
        request = AssistantRequest(text, self.attachments) if self.attachments else text
        self._request_id = self.controller.submit(request, self.desktop.context())
        if self.controller.waiting:
            self._pipeline_status(self._request_id, "Routing · waiting for previous connection…")
