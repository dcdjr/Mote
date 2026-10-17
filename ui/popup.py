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
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLayout,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ai.pipeline import MotePipeline
from ai.worker import PipelineController
from integration.desktop import DesktopIntegration


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
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 28, 28)


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
    FOLLOW_LAG = 0.12

    def __init__(self, *, pipeline=None) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Mote")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont("Segoe UI", 11))
        self._expanded = False
        self.state = PopupState.CLOSED
        self.motion_enabled = _motion_enabled()
        self._response_height = 100
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
        self._expansion = QPropertyAnimation(self, b"geometry", self)
        self._expansion.setEasingCurve(QEasingCurve.Type.InOutCubic)
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
        self.controller.stopped.connect(QApplication.instance().quit)
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
            QPushButton#sendButton {
                background-color: #dce4ed;
                color: #242628;
                padding: 0 12px;
            }
            QPushButton#sendButton:pressed { background-color: #b9c7d6; }
        """)

        self.input_bar = _InputSurface(self)
        self.input_bar.setObjectName("inputBar")
        self.input_bar.setFixedHeight(self.INPUT_HEIGHT)
        self.prompt = QLineEdit()
        self.prompt.installEventFilter(self.input_bar)
        self.prompt.setPlaceholderText("Ask Mote anything...")
        self.prompt.setAccessibleName("Ask Mote anything")
        self.prompt.setToolTip("Enter to send · Ask about your screen for automatic capture · Esc to hide · Ctrl+Q to quit")
        self.prompt.returnPressed.connect(self.submit)
        entry = QVBoxLayout(self.input_bar)
        entry.setContentsMargins(20, 10, 20, 12)
        entry.setSpacing(6)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        for glyph, name in (("\ue720", "Microphone"), ("\ue710", "Add attachment")):
            button = QPushButton(glyph)
            button.setFont(QFont("Segoe MDL2 Assets", 12))
            button.setFixedSize(32, 32)
            button.setAccessibleName(name)
            button.setToolTip(f"{name} (coming soon)")
            button.setEnabled(False)
            toolbar.addWidget(button)
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
        toolbar.addStretch()
        self.send_button = _SendButton(self)
        self.send_button.setObjectName("sendButton")
        self.send_button.setFixedHeight(32)
        self.send_button.setAccessibleName("Send prompt")
        self.send_button.setToolTip("Send prompt (Enter)")
        self.send_button.clicked.connect(self.submit)
        toolbar.addWidget(self.send_button)
        entry.addLayout(toolbar)
        entry.addWidget(self.prompt)

        self.response = QPlainTextEdit()
        self.response.setObjectName("response")
        self.response.setReadOnly(True)
        self.response.setAccessibleName("Mote response")
        self.response.setMinimumHeight(0)
        self.response.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        # The viewport must not paint an opaque rectangle over the glass surface.
        self.response.viewport().setAutoFillBackground(False)
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
        self.escape.activated.connect(self.dismiss)
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

    def event(self, event) -> bool:
        result = super().event(event)
        # Defer until Qt has finished creating/applying the native window styles.
        if event.type() in (QEvent.Type.WinIdChange, QEvent.Type.Show) and hasattr(self, "_backdrop_timer"):
            self._backdrop_timer.start(0)
        return result

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
        bounds = screen.availableGeometry()
        height = self.INPUT_HEIGHT
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
        x = cursor.x() + self.CURSOR_OFFSET
        y = cursor.y() + self.CURSOR_OFFSET
        if x + self.width() > bounds.right() + 1:
            x = cursor.x() - self.width() - self.CURSOR_OFFSET
        if y + self.height() > bounds.bottom() + 1:
            y = cursor.y() - self.height() - self.CURSOR_OFFSET
        self.move(x, y)
        self._clamp_to_screen(screen)

    def _set_follow_enabled(self, enabled: bool) -> None:
        self.follow_enabled = enabled
        self.follow_button.setToolTip("Disable cursor following" if enabled else "Enable cursor following")
        self.follow_button.setAccessibleDescription("Enabled" if enabled else "Disabled")
        if enabled:
            self._start_following()
        else:
            self._stop_following()

    def _start_following(self) -> None:
        self._follow_position = None
        self._outside_since = None
        self._follow_paused = False
        self._follow_last_tick = time.monotonic()
        if self.follow_enabled and self.isVisible() and self.state != PopupState.CLOSING:
            self._follow_timer.start()

    def _stop_following(self) -> None:
        self._follow_timer.stop()
        self._follow_position = None
        self._outside_since = None

    def _pause_following(self) -> None:
        self._follow_paused = True
        self._outside_since = None
        self._follow_position = None

    def _follow_cursor(self) -> None:
        if not self.isVisible() or not self.follow_enabled or self.state == PopupState.CLOSING:
            self._stop_following()
            return
        if self.desktop.busy:
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
            self._follow_position = None
            return
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        width = min(self.WIDTH + 2 * self.SHADOW_MARGIN, bounds.width())
        natural_height = self.INPUT_HEIGHT + 2 * self.SHADOW_MARGIN
        if self._expanded:
            natural_height += self.PANEL_GAP + self._response_height
        height = min(natural_height, bounds.height())
        x = cursor.x() + self.CURSOR_OFFSET
        y = cursor.y() + self.CURSOR_OFFSET
        if x + width > bounds.right() + 1:
            x = cursor.x() - width - self.CURSOR_OFFSET
        if y + height > bounds.bottom() + 1:
            y = cursor.y() - height - self.CURSOR_OFFSET
        target = QPointF(
            max(bounds.left(), min(x, bounds.right() - width + 1)),
            max(bounds.top(), min(y, bounds.bottom() - height + 1)),
        )
        if self._follow_position is None:
            self._follow_position = QPointF(self.pos())
        alpha = 1.0 if not self.motion_enabled else 1.0 - math.exp(-elapsed / self.FOLLOW_LAG)
        position = self._follow_position + (target - self._follow_position) * alpha
        if (target - position).manhattanLength() < 0.5:
            position = target
        self._follow_position = position
        rect = QRect(round(position.x()), round(position.y()), width, height)
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

    def _request_quit(self) -> None:
        self._cancel_response()
        self.prompt.setEnabled(False)
        self.send_button.setEnabled(False)
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
        height = self.INPUT_HEIGHT + 2 * self.SHADOW_MARGIN
        if response_height is not None:
            height += self.PANEL_GAP + response_height
        target = QRect(self.geometry())
        target.setHeight(height)
        return self._bounded_rect(target)

    def _expand_to(self, height) -> None:
        self._window_transition.stop()
        self.setWindowOpacity(1.0)
        self._expansion.stop()
        self._response_height = height
        self._expanded = True
        self.state = PopupState.EXPANDING
        self.response.show()
        self._expansion.setStartValue(self.geometry())
        self._rest_geometry = self._target_geometry(height)
        self._expansion.setEndValue(self._rest_geometry)
        self._expansion.setDuration(300 if self.motion_enabled else 0)
        self._expansion.start()

    def _expansion_finished(self) -> None:
        if self.state == PopupState.EXPANDING:
            self.state = PopupState.EXPANDED
            if self._stream_buffer or self._stream_done:
                self._start_stream_reveal()

    def _pipeline_status(self, request_id, text) -> None:
        if request_id != self._request_id:
            return
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
            self._finish_response_size()

    def _finish_response_size(self) -> None:
        document = QTextDocument()
        document.setDefaultFont(self.response.font())
        document.setPlainText(self.response.toPlainText())
        document.setTextWidth(max(80, self.response.width() - 52))
        height = min(self.RESPONSE_HEIGHT, max(100, int(document.size().height()) + 44))
        if height != self._response_height:
            self._expand_to(height)

    def _pipeline_completed(self, request_id, text) -> None:
        if request_id != self._request_id:
            return
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

    def _pipeline_failed(self, request_id, text) -> None:
        if request_id != self._request_id:
            return
        self._thinking_fade.stop()
        self.thinking.pulse.stop()
        self.thinking.hide()
        self._stream_timer.stop()
        self._stream_buffer.clear()
        self._stream_started = True
        self._stream_done = True
        self._reveal.stop()
        self._reveal_text(1.0)
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
        self._cancel_response()
        self.response.clear()
        self.request_state = "Routing"
        self.thinking.text = "Routing…"
        self.thinking.opacity = 1.0
        self.thinking.show()
        if self.motion_enabled:
            self.thinking.pulse.start()
        # Reserve a response panel once; token updates only append text.
        self._expand_to(self.RESPONSE_HEIGHT)
        self._request_id = self.controller.submit(text, self.desktop.context())
        if self.controller.waiting:
            self._pipeline_status(self._request_id, "Routing · waiting for previous connection…")
