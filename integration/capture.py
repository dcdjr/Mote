"""Native capture exclusion and compositor synchronization; no screen reads here."""
import ctypes
from ctypes import wintypes
import logging
import sys

logger = logging.getLogger(__name__)
WDA_EXCLUDEFROMCAPTURE = 0x11


def _windows_capture_api():
    if sys.platform != 'win32' or sys.getwindowsversion().build < 19041:
        return None
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowDisplayAffinity.restype = wintypes.BOOL
    api.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    api.SetWindowDisplayAffinity.restype = wintypes.BOOL
    return api


def exclude_from_capture(window):
    """Return a restore token when Windows accepts exclusion; opacity is the fallback."""
    try:
        api = _windows_capture_api()
        if api is None:
            return None
        hwnd = int(window.winId())
        previous = wintypes.DWORD()
        if not api.GetWindowDisplayAffinity(hwnd, ctypes.byref(previous)):
            return None
        if not api.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            return None
        return api, hwnd, previous.value
    except (OSError, AttributeError, ValueError):
        logger.debug('Native capture exclusion unavailable; using transparency fallback', exc_info=True)
        return None


def restore_capture_exclusion(token):
    if token is not None:
        api, hwnd, previous = token
        try:
            if not api.SetWindowDisplayAffinity(hwnd, previous):
                logger.warning('Could not restore capture affinity for Mote window')
        except (OSError, AttributeError):
            logger.debug('Capture affinity restore failed', exc_info=True)


def sync_compositor():
    """Wait for submitted opacity/geometry changes to reach Windows composition."""
    if sys.platform != 'win32':
        return
    try:
        dwm = ctypes.WinDLL('dwmapi')
        dwm.DwmFlush.argtypes = []
        dwm.DwmFlush.restype = ctypes.c_long
        dwm.DwmFlush()
    except (OSError, AttributeError):
        # The Qt timer still allows the compositor a frame when DWM is unavailable.
        logger.debug('DWM flush unavailable', exc_info=True)
