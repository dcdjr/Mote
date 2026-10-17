"""Discover Windows launch targets locally; model text is only a lookup key."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from dataclasses import dataclass

from ai.pipeline import PipelineError


@dataclass(frozen=True)
class InstalledApp:
    name: str
    target: str
    kind: str = 'file'


def _key(name):
    return re.sub(r'[^\w]', '', name.casefold())


ALIASES = {'vscode': 'visualstudiocode', 'microsoftword': 'word',
           'microsoftexcel': 'excel', 'microsoftpowerpoint': 'powerpoint',
           'mozillafirefox': 'firefox', 'googlechrome': 'chrome', 'microsoftedge': 'edge'}


def _canonical(name):
    key = _key(name)
    return ALIASES.get(key, key)


def discover_apps():
    if sys.platform != 'win32':
        return []
    apps = []
    # Both per-user and machine-wide shortcuts, including nested Start-menu folders.
    for variable, suffix in [('APPDATA', 'Microsoft/Windows/Start Menu/Programs'),
                             ('PROGRAMDATA', 'Microsoft/Windows/Start Menu/Programs')]:
        if os.getenv(variable):
            root = Path(os.environ[variable]) / suffix
            for path in root.rglob('*.lnk'):
                apps.append(InstalledApp(path.stem, str(path)))
    # Fixed script: no request/model text is ever inserted into PowerShell.
    powershell = Path(os.getenv('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command',
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
            capture_output=True, encoding='utf-8-sig', errors='replace', timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
        records = json.loads(result.stdout or '[]')
        if isinstance(records, dict):
            records = [records]
        for item in records:
            if isinstance(item, dict) and item.get('Name') and item.get('AppID'):
                apps.append(InstalledApp(item['Name'], item['AppID'], 'aumid'))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass  # Start-menu shortcuts and registered App Paths remain available.
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\App Paths', 0, winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        name = winreg.EnumKey(root, index)
                        try:
                            with winreg.OpenKey(root, name) as entry:
                                target = os.path.expandvars(winreg.QueryValueEx(entry, '')[0]).strip('"')
                                if target.lower().endswith('.exe') and Path(target).is_file():
                                    apps.append(InstalledApp(Path(name).stem, target))
                        except OSError:
                            continue
            except OSError:
                continue
    return apps


class AppCatalog:
    def __init__(self, discover=discover_apps):
        self.discover = discover
        self._apps = None
        self._updated = 0

    def refresh(self):
        self._apps = self.discover()
        self._updated = time.monotonic()

    def resolve(self, name):
        if self._apps is None or time.monotonic() - self._updated > 60:
            self.refresh()
        key = _key(name)
        exact = [app for app in self._apps if _canonical(app.name) == _canonical(name)]
        candidates = exact or [app for app in self._apps if len(key) >= 3 and key in _key(app.name)]
        # The same app may be registered by both shortcut and AUMID; prefer its shortcut.
        unique = {}
        for app in candidates:
            unique.setdefault(_key(app.name), app)
        if len(unique) == 1:
            return next(iter(unique.values()))
        if len(unique) > 1:
            names = ', '.join(sorted(app.name for app in unique.values())[:8])
            raise PipelineError(f'Multiple apps match "{name}": {names}. Please use the full app name.')
        raise PipelineError(f'Could not find an installed app named "{name}". Use its Start-menu name; portable apps need a Start-menu shortcut.')

    def launch(self, app):
        if app.kind == 'aumid':
            explorer = str(Path(os.getenv('SystemRoot', r'C:\Windows')) / 'explorer.exe')
            subprocess.Popen([explorer, 'shell:AppsFolder\\' + app.target])
        else:
            os.startfile(app.target)


_catalog = None


def get_catalog():
    global _catalog
    if _catalog is None:
        _catalog = AppCatalog()
    return _catalog
