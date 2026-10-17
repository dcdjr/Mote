# Mote

Windows interaction prototype: **Ctrl+Shift+Space** opens a rounded, translucent
PySide6 popup beside the cursor. It follows the pointer with a short, smooth
delay and pauses when you approach its controls. Send or Enter displays an
animated local echo; no AI model is connected.

## Run (Windows, Python 3.10+)

```powershell
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

The app starts hidden. Press **Ctrl+Shift+Space** from any application to open it
and focus the input. Press the same shortcut again to dismiss it. **Esc** or the
right-click menu's **Hide Mote** also dismisses it with the closing animation.
Ctrl+Shift alone has no Mote action.

The compact **pin toggle** enables/disables following and remembers its setting
until the app exits. Toggling does not reposition the window. Following pauses
within 45px of the visible surface, remains paused within a wider 65px zone, and
resumes after 320ms outside that zone. Mouse interaction and context menus also
pause movement; keyboard focus alone does not. Position updates yield briefly
to entrance/resize animations to avoid competing geometry changes.

Click into Mote and press **Ctrl+Q** to exit the program and release the global
hotkey. **Quit Mote** is also available in the right-click menu. An error dialog
explains if the opening shortcut is already registered.

For UI-only development on Windows, Linux, or macOS:

```sh
python main.py --preview
```

Preview adds a 650ms demo thinking delay and does not register the global
toggle shortcut. Escape and focused Ctrl+Q remain available.

## Layout

- `main.py`: startup and wiring.
- `integration/hotkey.py`: Windows hotkey registration and cleanup using the Qt event loop.
- `ui/popup.py`: popup, cursor positioning, input, and local echo.
- `tests/test_interaction.py`: headless cursor-following, toggle, animation, and shortcut checks.

The native integration follows [Windows RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)
and [Qt native event filtering](https://doc.qt.io/qt-6/qabstractnativeeventfilter.html).

## Manual Windows check

1. Run the app, focus another application, and press Ctrl+Shift+Space.
2. Check that the popup opens near the pointer and typing goes into its input.
3. Move the pointer away and confirm smooth following, then approach the popup
   and confirm it pauses so the input, Send button, and response can be used.
   Move away and confirm following resumes after a brief grace period.
4. Toggle the pin off/on, including while thinking, and confirm no immediate
   repositioning. Dismiss/reopen and confirm the toggle setting is preserved.
5. Submit text and confirm thinking, expansion, and the local echo reveal.
   Try a long prompt and confirm the response scrolls.
6. Press Esc, move the pointer, and invoke again. Repeat near all screen edges
   and on each monitor, including monitors with different scaling.
7. Hold the shortcut: it should open once per press. Click outside and invoke
   again to check that input focus returns.
8. Press Ctrl+Shift+Space again and confirm animated dismissal. Reopen, click
   into Mote, and press Ctrl+Q to exit. Ctrl+Shift alone should do nothing.
9. Rapidly toggle following, submit, and open/close during animations. Check
   mixed-DPI monitors and gaps between displays; cross-monitor travel can span
   displays temporarily, but the destination must fit the cursor's monitor.
10. Start a second instance: it should report a hotkey conflict. Quit the first
   instance, then restart to confirm the hotkey was released.

Run automated checks with:

```sh
python -m unittest discover -s tests -v
```

These tests do not move the system cursor or inject global keyboard input.
Cursor polling runs only while Mote is visible and following is enabled. No AI
inference, screenshots, clipboard access, or desktop automation is included.
