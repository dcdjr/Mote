"""Windows hotkey registration using Qt's existing native event loop."""

import ctypes
from ctypes import wintypes
import sys
from collections.abc import Callable

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication


class WindowsHotkey(QAbstractNativeEventFilter):
    HOTKEY_ID = 1
    WM_HOTKEY = 0x0312
    MODIFIERS = 0x0002 | 0x0004 | 0x4000  # Control, Shift, no key repeat
    VK_SPACE = 0x20

    def __init__(self, callback: Callable[[], None]) -> None:
        super().__init__()
        if sys.platform != "win32":
            raise RuntimeError("The global hotkey requires Windows. Use --preview to try the UI.")
        self._callback = callback
        self._app = None
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL

    def start(self, app: QApplication) -> None:
        if self._app is not None:
            return
        if not self._user32.RegisterHotKey(None, self.HOTKEY_ID, self.MODIFIERS, self.VK_SPACE):
            error = ctypes.WinError(ctypes.get_last_error())
            raise RuntimeError(
                f"Could not register Ctrl+Shift+Space. Another app or Mote instance may be using it.\n{error}"
            )
        self._app = app
        app.installNativeEventFilter(self)

    def stop(self) -> None:
        if self._app is not None:
            self._user32.UnregisterHotKey(None, self.HOTKEY_ID)
            self._app.removeNativeEventFilter(self)
            self._app = None

    def nativeEventFilter(self, event_type, message):
        if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self.WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                self._callback()
                return True, 0
        return False, 0
