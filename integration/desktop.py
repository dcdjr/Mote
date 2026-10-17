"""GUI-thread desktop bridge: in-memory screenshots, clipboard, and confirmation."""

from PySide6.QtCore import QBuffer, QIODevice, QObject, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QMessageBox

from ai.pipeline import Cancelled, PipelineError, validate_command
from integration.capture import exclude_from_capture, restore_capture_exclusion, sync_compositor
from integration.actions import (
    WindowsActions,
    activate_target,
    foreground_target,
    send_hotkey,
)


def capture_screen(screen):
    """Capture one Qt screen as PNG bytes without touching the filesystem."""
    pixmap = screen.grabWindow(0)
    if pixmap.isNull():
        raise PipelineError("Screen capture failed. Check screen-recording permissions or try on Windows.")
    image = pixmap.toImage()
    if max(image.width(), image.height()) > 1600:
        image = image.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise PipelineError("Could not encode the screen image.")
    return bytes(buffer.data())


class DesktopIntegration(QObject):
    def __init__(self, popup):
        super().__init__(popup)
        self.popup = popup
        self.actions = WindowsActions()
        self.target = None
        self.busy = False
        self._dialog = None
        self._capture_call = None
        self._capture_screen = None
        self._capture_windows = []
        self._capture_timer = QTimer(self)
        self._capture_timer.setSingleShot(True)
        self._capture_timer.timeout.connect(self._finish_capture)

    def remember_target(self):
        target = foreground_target()
        if target is not None:
            self.target = target

    def context(self):
        self.remember_target()
        screen = QApplication.screenAt(QCursor.pos()) or self.popup.screen()
        return {"target": self.target, "screen_name": screen.name()}

    def cancel_pending(self):
        if self._capture_call is not None:
            self._capture_timer.stop()
            self._capture_call.cancel.set()
            self._finish_capture()
        if self._dialog is not None:
            self._dialog.reject()

    def handle(self, request_id, call):
        if call.cancel.is_set():
            call.resolve(error=Cancelled())
            return
        try:
            if call.kind == "screenshot":
                self._screenshot(call)
            elif call.kind == "command":
                command = validate_command(call.payload)
                if command["name"] == "press_hotkey":
                    self._confirm_shortcut(request_id, call, command)
                else:
                    call.resolve(self.actions.execute(command, clipboard=QApplication.clipboard()))
            else:
                raise PipelineError("Unknown desktop operation; nothing was executed.")
        except Exception as error:  # noqa: BLE001 - forward GUI errors to the waiting worker
            call.resolve(error=error)

    def _screenshot(self, call):
        popup = self.popup
        screen = next((s for s in QApplication.screens() if s.name() == call.context["screen_name"]), None)
        if screen is None:
            raise PipelineError("The requested monitor was disconnected. Invoke Mote on an available screen.")
        self.busy = True
        popup._stop_following()
        popup._window_transition.stop()
        popup._expansion.stop()
        # Complete geometry motion before briefly hiding from the compositor.
        popup.setGeometry(popup._target_geometry(popup._response_height))
        opacity = 1.0 if popup.state.name == "OPENING" else popup.windowOpacity()
        popup.state = type(popup.state).EXPANDED
        self._capture_call = call
        self._capture_screen = screen
        # Include Mote-owned menus/dialogs, not only the main popup.
        windows = [popup, *(w for w in QApplication.topLevelWidgets() if w is not popup and w.isVisible())]
        self._capture_windows = [(w, opacity if w is popup else w.windowOpacity(), exclude_from_capture(w)) for w in windows]
        # Keep widgets logically visible: hide() would cancel this request.
        # Native exclusion plus opacity protects the underlying desktop pixels.
        for window, _, _ in self._capture_windows:
            window.setWindowOpacity(0.0)
        self._capture_timer.start(150)

    def _finish_capture(self):
        call, screen = self._capture_call, self._capture_screen
        if call is None:
            return
        self._capture_call = self._capture_screen = None
        self._capture_timer.stop()
        windows, self._capture_windows = self._capture_windows, []
        try:
            if call.cancel.is_set():
                raise Cancelled()
            sync_compositor()
            call.resolve(capture_screen(screen))
        except Exception as error:  # noqa: BLE001 - always restore opacity and unblock the worker
            call.resolve(error=error)
        finally:
            self.busy = False
            popup = self.popup
            for window, opacity, token in windows:
                restore_capture_exclusion(token)
                try:
                    if window.isVisible() and (window is not popup or popup.state.name not in ("CLOSED", "CLOSING")):
                        window.setWindowOpacity(opacity)
                except RuntimeError:
                    pass  # A transient menu/dialog may have been destroyed during the capture wait.
            if popup.isVisible() and popup.state.name not in ("CLOSED", "CLOSING"):
                popup._start_following()

    def _confirm_shortcut(self, request_id, call, command):
        target = call.context.get("target")
        if target is None:
            raise PipelineError("No previous application was recorded. Invoke Mote from the intended target app.")
        shortcut = command["arguments"]["shortcut"]
        dialog = QMessageBox(self.popup)
        dialog.setWindowTitle("Confirm keyboard shortcut")
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"Send {shortcut} to {target['title']}?\nThis may paste, undo, save, close a window, or change your desktop.")
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        self._dialog = dialog
        self.busy = True
        self.popup._pause_following()
        self.popup._pipeline_status(request_id, "Waiting for confirmation…")

        def finished(result):
            self._dialog = None
            self.busy = False
            dialog.deleteLater()
            if call.cancel.is_set():
                call.resolve(error=Cancelled())
                return
            if result != QMessageBox.StandardButton.Yes:
                call.resolve("Cancelled. No keyboard input was sent.")
                return
            try:
                activate_target(target)
            except Exception as error:  # noqa: BLE001 - Qt callbacks must resolve the waiting call
                call.resolve(error=error)
                return

            def send():
                try:
                    if call.cancel.is_set():
                        raise Cancelled()
                    validate_command(command)
                    call.resolve(send_hotkey(shortcut, target))
                except Exception as error:  # noqa: BLE001 - Qt callbacks must resolve the waiting call
                    call.resolve(error=error)

            QTimer.singleShot(100, self, send)

        dialog.finished.connect(finished)
        dialog.open()
