"""Native Windows hotkeys, reassigned without releasing the working binding first."""
import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, Qt
from PySide6.QtGui import QKeySequence

DEFAULT_SHORTCUT = 'Ctrl+Shift+Space'


def parse_shortcut(text):
    sequence = QKeySequence(text, QKeySequence.SequenceFormat.PortableText)
    if sequence.count() != 1:
        raise ValueError('Choose one shortcut, not a sequence of shortcuts.')
    combination = sequence[0]
    key = combination.key()
    modifiers = combination.keyboardModifiers()
    allowed = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.MetaModifier
    if modifiers & ~allowed or not modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
        raise ValueError('Include Ctrl, Alt, or Win plus a nonmodifier key.')
    value = key.value
    special = {Qt.Key.Key_Space: 0x20, Qt.Key.Key_Tab: 9, Qt.Key.Key_Return: 13,
               Qt.Key.Key_Backspace: 8, Qt.Key.Key_Delete: 0x2E, Qt.Key.Key_Insert: 0x2D,
               Qt.Key.Key_Home: 0x24, Qt.Key.Key_End: 0x23, Qt.Key.Key_PageUp: 0x21,
               Qt.Key.Key_PageDown: 0x22, Qt.Key.Key_Left: 0x25, Qt.Key.Key_Up: 0x26,
               Qt.Key.Key_Right: 0x27, Qt.Key.Key_Down: 0x28}
    if 0x30 <= value <= 0x39 or 0x41 <= value <= 0x5A:
        virtual_key = value
    elif Qt.Key.Key_F1.value <= value <= Qt.Key.Key_F24.value:
        virtual_key = 0x70 + value - Qt.Key.Key_F1.value
    elif key in special:
        virtual_key = special[key]
    else:
        raise ValueError('Use a letter, number, function key, Space, or navigation key.')
    canonical = sequence.toString(QKeySequence.SequenceFormat.PortableText)
    if canonical in ('Ctrl+Q', 'Ctrl+Alt+Del', 'Meta+L', 'Meta+U') or key == Qt.Key.Key_F12:
        raise ValueError('That shortcut is reserved by Mote or Windows.')
    native = 0x4000
    for flag, win_flag in ((Qt.KeyboardModifier.ControlModifier, 2), (Qt.KeyboardModifier.ShiftModifier, 4),
                           (Qt.KeyboardModifier.AltModifier, 1), (Qt.KeyboardModifier.MetaModifier, 8)):
        if modifiers & flag:
            native |= win_flag
    return canonical, native, virtual_key


class WindowsHotkey(QAbstractNativeEventFilter):
    HOTKEY_ID = 1
    WM_HOTKEY = 0x0312
    MODIFIERS = 0x0002 | 0x0004 | 0x4000
    VK_SPACE = 0x20

    def __init__(self, callback):
        super().__init__()
        if sys.platform != 'win32':
            raise RuntimeError('The global hotkey requires Windows. Use --preview to try the UI.')
        self._callback = callback
        self._app = None
        self._application = None
        self._hotkey_id = self.HOTKEY_ID
        self.shortcut = DEFAULT_SHORTCUT
        self._user32 = ctypes.WinDLL('user32', use_last_error=True)
        self._user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL

    def start(self, app, shortcut=DEFAULT_SHORTCUT):
        if self._app is not None:
            return
        self._application = app
        canonical, modifiers, key = parse_shortcut(shortcut)
        if not self._user32.RegisterHotKey(None, self._hotkey_id, modifiers, key):
            raise RuntimeError(f'Cannot register {canonical}. Another app may already use it. Choose another shortcut in Settings.')
        self.shortcut = canonical
        self._app = app
        app.installNativeEventFilter(self)

    def reassign(self, shortcut):
        canonical, modifiers, key = parse_shortcut(shortcut)
        if self._app is None:
            self.start(self._application, canonical)
            return canonical
        if canonical == self.shortcut:
            return canonical
        new_id = 2 if self._hotkey_id == 1 else 1
        if not self._user32.RegisterHotKey(None, new_id, modifiers, key):
            raise RuntimeError('Shortcut unavailable. Your previous shortcut is still active.')
        if not self._user32.UnregisterHotKey(None, self._hotkey_id):
            self._user32.UnregisterHotKey(None, new_id)
            raise RuntimeError('Could not release the old shortcut; the previous binding was preserved.')
        self._hotkey_id, self.shortcut = new_id, canonical
        return canonical

    def stop(self):
        if self._app is not None:
            self._user32.UnregisterHotKey(None, self._hotkey_id)
            self._app.removeNativeEventFilter(self)
            self._app = None

    def nativeEventFilter(self, event_type, message):
        if event_type in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == self.WM_HOTKEY and msg.wParam == self._hotkey_id:
                self._callback()
                return True, 0
        return False, 0
