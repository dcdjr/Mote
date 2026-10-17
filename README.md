# Mote

Minimal Windows interaction prototype: **Ctrl+Shift+Space** opens a frameless
PySide6 popup beside the cursor. It contains a text input and read-only response
area. Submit or Enter displays a local echo to check the interaction.

## Run (Windows, Python 3.10+)

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

The app starts hidden. Press **Ctrl+Shift+Space** from any application to open it
and focus the input. **Esc** or **Hide** hides it; the shortcut opens it again at
the current cursor position, preserving the text. **Quit** exits and releases
the shortcut. An error dialog explains if the shortcut is already registered.

For UI-only development on Windows, Linux, or macOS:

```sh
python main.py --preview
```

## Layout

- `main.py`: startup and wiring.
- `integration/hotkey.py`: Windows hotkey registration and cleanup using the Qt event loop.
- `ui/popup.py`: popup, cursor positioning, input, and local echo.

The native integration follows [Windows RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)
and [Qt native event filtering](https://doc.qt.io/qt-6/qabstractnativeeventfilter.html).

## Manual Windows check

1. Run the app, focus another application, and press Ctrl+Shift+Space.
2. Check that the popup opens near the pointer and typing goes into its input.
3. Submit text and confirm it appears as a local echo in the response area.
4. Press Esc, move the pointer, and invoke again. Repeat near all screen edges
   and on each monitor, including monitors with different scaling.
5. Hold the shortcut: it should open once per press. Click outside and invoke
   again to check that input focus returns.
6. Start a second instance: it should report a hotkey conflict. Quit the first
   instance, then restart to confirm the hotkey was released.

No AI inference, screenshots, clipboard access, or cursor polling is included.
