"""Run Mote on Windows, or --preview without a global hotkey on other desktops."""

import argparse
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from integration.hotkey import WindowsHotkey
from ui.popup import AssistantPopup


def main() -> int:
    parser = argparse.ArgumentParser(description="Mote local desktop assistant")
    parser.add_argument("--preview", action="store_true", help="Open UI without registering the global opening hotkey")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Mote")
    app.setQuitOnLastWindowClosed(False)
    try:
        popup = AssistantPopup()
    except (ValueError, OSError) as error:
        QMessageBox.critical(None, "Mote configuration error", str(error))
        return 1

    if args.preview:
        popup.open_at_cursor()
    else:
        try:
            hotkey = WindowsHotkey(popup.toggle_at_cursor)
            hotkey.start(app)
        except (OSError, RuntimeError) as error:
            QMessageBox.critical(None, "Mote could not start", str(error))
            return 1
        app.aboutToQuit.connect(hotkey.stop)
        print("Mote ready: Ctrl+Shift+Space toggles; Esc dismisses; Ctrl+Q quits when focused.", flush=True)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
