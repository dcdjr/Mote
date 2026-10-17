# Build on Windows only: python -m PyInstaller packaging/Mote.spec --noconfirm --clean
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all
from PySide6.QtCore import QLibraryInfo

if sys.platform != 'win32':
    raise SystemExit('A Windows executable must be built on Windows.')
root = Path(SPECPATH).parent
hiddenimports = ['PySide6.QtSvg', 'PySide6.QtTextToSpeech', 'pycaw.pycaw', 'comtypes.client']
datas = [(str(root / 'assets'), 'assets')]
binaries = []
for package in ('faster_whisper', 'ctranslate2', 'tokenizers', 'onnxruntime', 'av',
                'sounddevice', '_sounddevice_data', 'pypdf', 'pycaw', 'comtypes', 'huggingface_hub'):
    data, binary, imports = collect_all(package)
    datas += data
    binaries += binary
    hiddenimports += imports
plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
for category in ('platforms', 'imageformats', 'styles', 'texttospeech'):
    source = plugins / category
    if source.exists():
        datas.append((str(source), 'PySide6/plugins/' + category))
a = Analysis([str(root / 'main.py')], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, excludes=['tkinter', 'pytest'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Mote', debug=False,
          strip=False, upx=False, console=False, icon=str(root / 'assets/mote.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Mote')
