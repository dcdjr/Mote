"""Mote application entry point, shared by source and the Windows installer."""
import argparse
import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from ai.gemma import GemmaBackend
from ai.pipeline import MotePipeline
from config import APP_ID, VERSION, Preferences
from integration.hotkey import DEFAULT_SHORTCUT, WindowsHotkey
from ui.popup import AssistantPopup


def verify_runtime_dependencies():
    """Keep these imports explicit in the smoke contract, without touching devices."""
    from importlib import import_module
    for name in ('av', 'ctranslate2', 'faster_whisper', 'numpy', 'pypdf', 'sounddevice', 'PySide6.QtTextToSpeech'):
        import_module(name)
    if sys.platform == 'win32':
        import_module('pycaw.pycaw')
        import_module('comtypes')
        speech = import_module('PySide6.QtTextToSpeech')
        if 'sapi' not in speech.QTextToSpeech.availableEngines():
            raise RuntimeError('The packaged Windows SAPI speech plugin is missing.')


def main():
    parser = argparse.ArgumentParser(description='Mote local desktop assistant')
    parser.add_argument('--preview', action='store_true', help='Open without a Windows global hotkey')
    parser.add_argument('--smoke-test', action='store_true', help='Verify packaged imports and GUI startup, then exit')
    args = parser.parse_args()
    if args.smoke_test:
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    if sys.platform == 'win32':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setOrganizationName('Mote')
    app.setApplicationName('Mote')
    app.setApplicationVersion(VERSION)
    app.setQuitOnLastWindowClosed(False)
    if args.smoke_test:
        try:
            verify_runtime_dependencies()
        except Exception:  # noqa: BLE001 - smoke failures must produce a nonzero exit without a frozen-app error dialog
            import os
            import tempfile
            import traceback
            report = Path(os.getenv('MOTE_SMOKE_REPORT', str(Path(tempfile.gettempdir()) / 'Mote-smoke-error.txt')))
            report.write_text(traceback.format_exc(), encoding='utf-8')
            return 1
    resource_root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    app.setWindowIcon(QIcon(str(resource_root / 'assets/mote.ico')))
    preferences = Preferences()
    try:
        popup = AssistantPopup(pipeline=MotePipeline(backend=GemmaBackend(model=preferences.model)))
    except (ValueError, OSError) as error:
        QMessageBox.critical(None, 'Mote configuration error', str(error))
        return 1
    popup.setWindowIcon(app.windowIcon())
    hotkey, shortcut_error = None, ''
    if sys.platform == 'win32' and not args.preview and not args.smoke_test:
        hotkey = WindowsHotkey(popup.toggle_at_cursor)
        try:
            hotkey.start(app, preferences.shortcut)
        except (ValueError, RuntimeError) as error:
            shortcut_error = str(error)
            if preferences.shortcut != DEFAULT_SHORTCUT:
                try:
                    hotkey.start(app, DEFAULT_SHORTCUT)
                except RuntimeError:
                    pass
        app.aboutToQuit.connect(hotkey.stop)
    elif not args.preview and not args.smoke_test:
        QMessageBox.critical(None, 'Mote', 'Global hotkeys require Windows. Launch with --preview on Linux/macOS.')
        return 1
    popup.configure_preferences(preferences, hotkey)
    tray = QSystemTrayIcon(app.windowIcon(), app)
    tray.setToolTip('Mote · local desktop assistant')
    menu = QMenu()
    menu.addAction('Open Mote', popup.open_at_cursor)
    menu.addAction('Settings', popup.open_settings)
    menu.addSeparator()
    menu.addAction('Quit Mote', popup._request_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: popup.open_at_cursor() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    if not args.smoke_test and QSystemTrayIcon.isSystemTrayAvailable():
        tray.show()
    app.aboutToQuit.connect(tray.hide)
    if args.smoke_test:
        popup.open_at_cursor()
        QTimer.singleShot(250, popup._request_quit)
    elif args.preview:
        popup.open_at_cursor()
    elif shortcut_error or str(preferences.get('setup_seen','false')).lower() not in ('true','1'):
        QTimer.singleShot(0, popup.open_settings)
    if shortcut_error:
        QTimer.singleShot(0, lambda: QMessageBox.warning(popup, 'Choose a shortcut', shortcut_error))
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
