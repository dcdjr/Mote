"""Small allowlisted Windows executor. No shell, model code, or arbitrary paths."""

import ctypes
import os
import sys
from ctypes import wintypes
from urllib.parse import urlsplit

from ai.pipeline import PipelineError, validate_command

APPLICATIONS = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "chrome": "chrome.exe",
    "edge": "microsoft-edge:", "file_explorer": "explorer.exe",
    "settings": "ms-settings:", "spotify": "spotify:",
}
SETTINGS = {
    "display": "display", "sound": "sound", "bluetooth": "bluetooth",
    "network": "network-status", "wifi": "network-wifi", "privacy": "privacy",
    "accessibility": "easeofaccess", "windows_update": "windowsupdate",
}
HOTKEYS = {
    "CTRL+C": (0x11, 0x43), "CTRL+V": (0x11, 0x56), "CTRL+Z": (0x11, 0x5A),
    "CTRL+A": (0x11, 0x41), "CTRL+S": (0x11, 0x53), "ALT+TAB": (0x12, 0x09),
    "ALT+F4": (0x12, 0x73), "WIN+D": (0x5B, 0x44), "WIN+E": (0x5B, 0x45),
}


def _user32():
    if sys.platform != "win32":
        raise PipelineError("Windows desktop commands are unavailable in Linux/macOS preview.")
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetForegroundWindow.restype = wintypes.HWND
    api.IsWindow.argtypes = [wintypes.HWND]
    api.IsWindow.restype = wintypes.BOOL
    api.SetForegroundWindow.argtypes = [wintypes.HWND]
    api.SetForegroundWindow.restype = wintypes.BOOL
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowThreadProcessId.restype = wintypes.DWORD
    api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    api.GetWindowTextW.restype = ctypes.c_int
    api.GetAsyncKeyState.argtypes = [ctypes.c_int]
    api.GetAsyncKeyState.restype = ctypes.c_short
    return api


def foreground_target():
    if sys.platform != "win32":
        return None
    api = _user32()
    hwnd = api.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return None
    title = ctypes.create_unicode_buffer(256)
    api.GetWindowTextW(hwnd, title, len(title))
    return {"hwnd": hwnd, "pid": pid.value, "title": title.value or "previous application"}


def _validate_target(api, target):
    if not target or not api.IsWindow(target["hwnd"]):
        raise PipelineError("The original application is no longer available. Reopen Mote from the target app.")
    pid = wintypes.DWORD()
    api.GetWindowThreadProcessId(target["hwnd"], ctypes.byref(pid))
    if pid.value != target["pid"] or pid.value == os.getpid():
        raise PipelineError("The target window changed. No keys were sent.")


def activate_target(target):
    api = _user32()
    _validate_target(api, target)
    if not api.SetForegroundWindow(target["hwnd"]):
        raise PipelineError("Windows blocked focus restoration. Focus the target app and invoke Mote again.")


def send_hotkey(shortcut, target):
    api = _user32()
    _validate_target(api, target)
    if api.GetForegroundWindow() != target["hwnd"]:
        raise PipelineError("Focus changed before the shortcut. No keys were sent.")
    if any(api.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
        raise PipelineError("Release modifier keys and try again. No keys were sent.")

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]

    api.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    api.SendInput.restype = wintypes.UINT
    keys = HOTKEYS[shortcut]
    events = [INPUT(type=1, data=INPUTUNION(ki=KEYBDINPUT(wVk=key))) for key in keys]
    releases = [INPUT(type=1, data=INPUTUNION(ki=KEYBDINPUT(wVk=key, dwFlags=2))) for key in reversed(keys)]
    inputs = (INPUT * (len(events) + len(releases)))(*events, *releases)
    if api.SendInput(len(inputs), inputs, ctypes.sizeof(INPUT)) != len(inputs):
        # Release any keys accepted before a partial failure.
        api.SendInput(len(releases), (INPUT * len(releases))(*releases), ctypes.sizeof(INPUT))
        raise PipelineError("Windows blocked some input (possibly an elevated app). The shortcut may be incomplete.")
    return f"Sent {shortcut} to {target['title']}."


class WindowsActions:
    def execute(self, command, *, clipboard=None):
        command = validate_command(command)  # Revalidate at the execution boundary.
        name, arguments = command["name"], command["arguments"]
        if sys.platform != "win32":
            raise PipelineError("Windows desktop commands are unavailable in Linux/macOS preview.")
        if name == "copy_text":
            if clipboard is None:
                raise PipelineError("Clipboard is unavailable.")
            clipboard.setText(arguments["text"])
            return "Copied the requested text to the clipboard."
        if name in ("set_volume", "adjust_volume"):
            raise PipelineError("Volume control is not integrated yet. Ask Mote to open sound settings instead.")
        if name == "press_hotkey":
            raise PipelineError("Keyboard shortcuts require confirmation and a verified target window.")
        if name == "open_application":
            destination = APPLICATIONS[arguments["application"]]
        elif name == "open_windows_settings":
            destination = "ms-settings:" + SETTINGS[arguments["page"]]
        elif name == "open_website":
            destination = arguments["url"]
            parsed = urlsplit(destination)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                    or parsed.password or "\\" in destination
                    or any(ord(char) < 33 or ord(char) == 127 for char in destination)):
                raise PipelineError("Only valid HTTP/HTTPS website addresses without credentials are allowed.")
            try:
                _ = parsed.port  # Access validates the port's syntax and range.
            except ValueError:
                raise PipelineError("The website address has an invalid port.") from None
        else:
            raise PipelineError("This command is not implemented; nothing was executed.")
        try:
            os.startfile(destination)
        except OSError as error:
            raise PipelineError("Windows could not open the requested app/page. Check that the app is installed.") from error
        return f"Asked Windows to open {arguments.get('application') or arguments.get('page') or destination}."
