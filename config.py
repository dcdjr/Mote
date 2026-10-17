"""Small per-user settings store; no files are written beside the executable."""
import os

from PySide6.QtCore import QSettings

VERSION = '0.2.0'
APP_ID = 'Mote.Desktop.Assistant'
DEFAULT_SHORTCUT = 'Ctrl+Shift+Space'


class Preferences:
    def __init__(self, path=None):
        self.store = QSettings(str(path), QSettings.Format.IniFormat) if path else QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, 'Mote', 'Mote')

    def get(self, key, default=None):
        return self.store.value(key, default)

    def set(self, key, value):
        self.store.setValue(key, value)
        self.store.sync()
        if self.store.status() != QSettings.Status.NoError:
            raise OSError('Could not save settings. Check permissions on your user settings folder.')

    @property
    def model(self):
        return str(self.get('model', os.getenv('MOTE_MODEL', 'gemma4:e2b')))

    @property
    def shortcut(self):
        return str(self.get('shortcut', DEFAULT_SHORTCUT))

    @property
    def opacity(self):
        try:
            return max(60, min(100, int(self.get('opacity', 100))))
        except (ValueError, TypeError):
            return 100

    @property
    def follow(self):
        return str(self.get('follow', 'true')).lower() not in ('false', '0')
