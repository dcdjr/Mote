"""Exercise the real application event loop in an isolated offscreen process."""

import os
import subprocess
import sys
import unittest
from pathlib import Path


class StartupTests(unittest.TestCase):
    def test_preview_starts_and_focused_quit_exits(self):
        script = """
import sys
from unittest.mock import patch
from PySide6.QtCore import QTimer
from ui.popup import AssistantPopup
import main

original_open = AssistantPopup.open_at_cursor
def open_then_quit(popup):
    original_open(popup)
    QTimer.singleShot(100, popup.quit_shortcut.activated.emit)

with patch.object(sys, 'argv', ['main.py', '--preview']), patch.object(AssistantPopup, 'open_at_cursor', open_then_quit):
    result = main.main()
assert result == 0
print('preview exited cleanly')
"""
        result = subprocess.run(
            [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
            capture_output=True, text=True, timeout=8, check=True,
        )
        self.assertIn('preview exited cleanly', result.stdout)
        self.assertNotIn('QThread: Destroyed', result.stderr)


if __name__ == '__main__':
    unittest.main()
