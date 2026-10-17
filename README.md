# Mote

Mote is a local Windows desktop assistant beside your cursor. Press
**Ctrl+Shift+Space**, ask a question, and receive a streamed Gemma response.
The existing AI router selects an answer, reasoning, screen context, or one
supported Windows action. Routing and inference run off the GUI thread.

## Windows setup

Install **Python 3.10+** and [Ollama for Windows](https://ollama.com/download/windows).
Start Ollama, then install the model and Python dependencies:

```powershell
ollama pull gemma4:e2b
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Ollama's Windows app normally runs its local API in the background. If needed,
run `ollama serve`. Mote uses that existing API; there is no separate Python
server to launch. Python dependencies are PySide6 and the Ollama Python client
(which installs HTTPX and Pydantic).

The app starts hidden. Model loading happens on the first request, so the first
answer may take longer. No models are downloaded by Mote.

## Use

- **Ctrl+Shift+Space** toggles Mote at the cursor; **Esc** dismisses it.
- Type a request and press **Enter** or **Send**. Status indicates routing,
  thinking, capturing, execution, or confirmation. Answers stream into the panel.
- Ask another question to continue; three recent turns of bounded text context
  are retained in memory. A new submission replaces pending work and cancels
  earlier output. Dismissal also cancels the current request; reopening is safe.
- The **pin toggle** enables/disables cursor following and remembers its setting
  for the session. Following pauses near controls, during mouse interaction,
  during menus, and while opening/expanding. Keyboard focus alone does not pause it.
- Select response text and use **Ctrl+C** or the editor's right-click Copy action.
- **Ctrl+Q**, while Mote is focused, or **Quit Mote** in its right-click menu exits
  and releases the global hotkey. If a model connection is pending, shutdown
  waits for it to return or time out while keeping the event loop alive.

Examples: “Explain recursion briefly”, “Help me debug this code: …”,
“What does the error on my screen mean?”, “Open Notepad”,
“Open https://example.org”, or “Copy this text: Hello from Mote”.

## Screen context and model capabilities

Only a router decision requesting screen context triggers a capture. Mote
captures the **monitor containing the cursor at submission**, briefly makes its
own window transparent, then restores its opacity and following. Image data is
encoded in memory, resized to at most 1600px on its longest side, sent to the
selected local model, and discarded after the request. Images are not saved or
retained in conversation history.

The installed model/runtime must advertise `vision` in Ollama's `/api/show`
response. Some `gemma4:e2b` installations advertise text/thinking capabilities
without vision. Mote reports this clearly rather than sending an unsupported
image request. To use screen analysis, install a vision-capable local Gemma
variant and select it as the answer model. For example, **if your installation
advertises vision for `gemma4:12b`**:

```powershell
ollama pull gemma4:12b
$env:MOTE_MODEL = "gemma4:12b"
.venv\Scripts\python.exe main.py
```

Reasoning controls follow the router's decision when supported. Capability
metadata is cached; unsupported reasoning controls fall back to the model's
normal mode, with a status explanation. Model `thinking` fields stay separate
from the displayed answer and conversation memory. The API behavior follows
[Ollama's thinking documentation](https://docs.ollama.com/capabilities/thinking)
and [chat API](https://docs.ollama.com/api/chat).

## Windows commands

Only the router's declared names and schema-validated arguments are accepted.
The executor validates them again before acting. It never runs model-generated
shell commands, Python code, arbitrary executables, or command-line arguments.

| Command | Available behavior |
| --- | --- |
| `open_application` | Notepad, Calculator, Chrome, Edge, File Explorer, Settings, Spotify; apps must be installed. |
| `open_website` | Open an HTTP/HTTPS URL in the default browser; other schemes and credentials are rejected. |
| `open_windows_settings` | Display, sound, Bluetooth, network, Wi-Fi, privacy, accessibility, Windows Update. |
| `copy_text` | Copy the supplied text to the Windows clipboard. |
| `press_hotkey` | Ctrl+C/V/Z/A/S, Alt+Tab/F4, Win+D/E; **requires explicit confirmation**, default Cancel. |
| `set_volume`, `adjust_volume` | Not integrated; Mote explains this and suggests opening sound settings. |

Keyboard shortcuts target the application recorded before Mote opened. The
confirmation names that window. After approval, Mote restores and verifies that
window's foreground focus and process identity, then sends only the allowlisted
shortcut. If focus restoration fails, the target closes/changes, modifiers are
held, or Windows blocks injection, it reports the problem. A successful send
means Windows accepted the input, not that the target application completed an
operation. These checks follow the [Windows SendInput API](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput).

## Configuration

Set variables before launching Mote; restart after changing them.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MOTE_MODEL` | `gemma4:e2b` | Answer model, including images when supported. |
| `MOTE_ROUTER_MODEL` | `gemma4:e2b` | Structured decision model; no separate FunctionGemma checkpoint is used. |
| `MOTE_OLLAMA_HOST` | `http://localhost:11434` | Existing Ollama endpoint, shared by router and answers. |
| `MOTE_TIMEOUT` | `60` | Request/read timeout in seconds, positive and finite. |

By default routing and answers use the same model with a 30-minute keep-alive,
without duplicate model instances or loading at startup. Choosing different
models may increase memory use and latency. Use local models and the local
endpoint to keep prompts and screenshots on your machine.

## Linux preview and automated tests

Install the same dependencies in a virtual environment, then run:

```sh
python main.py --preview
python -m unittest discover -s tests -v
```

`--preview` opens the real UI and uses the real local pipeline, with no global
Windows hotkey. It has no simulated responses or artificial thinking delay.
Windows commands report that they are unavailable. Screen capture depends on
Qt support and desktop permissions; restricted Wayland environments may refuse
capture.

Tests mock Ollama, screens, clipboard, and desktop input. They do not download
models, capture real screens, open apps, or inject actual keys. Qt interaction
tests use the offscreen platform. Offscreen warnings about opacity/raising are
expected; that backend has no window manager and cannot verify native focus,
Acrylic, or real screen capture.

## Manual Windows verification

1. Start Ollama and Mote. Focus Notepad, then invoke Ctrl+Shift+Space. Verify
   cursor-relative placement and input focus. Repeat the shortcut to dismiss;
   reopen and try Esc. Holding the hotkey should not repeatedly toggle it.
2. Ask “What is 2 plus 2? Reply briefly.” Verify real text arrives incrementally
   and the window remains responsive. Ask a follow-up without restarting.
   Try a debugging or math prompt and verify thinking text is not shown.
3. With a vision-capable answer model, put a visible error or paragraph on a
   second monitor. Invoke and ask about the screen with the pointer on that
   monitor. Verify capture status, correct monitor content, exclusion of Mote,
   restored opacity, and a real visual answer. Check that no image file appears.
4. Ask to open Notepad, a website, and Wi-Fi settings; verify each target. Ask
   to copy a short supplied string, then paste it manually into Notepad.
5. In a disposable Notepad window, request a supported shortcut. Cancel once:
   nothing should happen. Approve once: the named target should receive it.
   Test Alt+F4 only in a disposable window. Close/change the target during
   confirmation and check that Mote refuses to send keys. Test an elevated app
   and held modifiers; failures must explain what happened.
6. Submit, dismiss while routing/thinking/streaming, reopen, and submit a new
   request. Check that earlier text/errors never overwrite the newer result.
   Quit during a pending request and verify clean shutdown without a QThread
   warning. Restart and confirm the hotkey was released.
7. Toggle following off/on, approach its controls, drag/select response text,
   and open the right-click menu. Check following pauses and resumes without
   jumps. Repeat during response expansion and rapid open/close operations.
8. Check every edge on each monitor, including mixed scaling and a monitor
   above/left of the primary display. The destination must fit usable bounds.
   Test Windows reduced-motion settings: opening, expansion, and reveal should
   stop animating; the thinking indicator should stay static.
9. Inspect transparent corners and the gap between panels on Windows 10/11.
   Existing DWM Acrylic probing/translucent fallback is preserved; layered Qt
   windows intentionally keep the fallback when a whole-window blur would
   damage alpha corners. Native appearance requires visual inspection.
10. Stop Ollama, select a missing model, and try a request with a short
    `MOTE_TIMEOUT`. Verify actionable errors and recovery after fixing the
    configuration/restarting Ollama. Start two Mote instances to verify the
    hotkey-conflict error.

## Scope and limitations

- Microphone input, file attachments, and volume control are not implemented.
  Automatic screen routing replaces the old disabled screenshot placeholder.
- The router accepts a 4KB request/context budget; long pasted requests may need
  shortening. Conversation memory is limited to six messages / 12K characters
  and disappears on exit. Images from prior turns are not reused.
- Cancellation is cooperative. An in-flight blocking model call may need to
  return or reach `MOTE_TIMEOUT` before the next queued request starts or Mote
  exits. Only one worker runs at a time, keeping model use and memory bounded.
- Native hotkeys, focus restoration, input injection, capture exclusion,
  mixed-DPI rendering, and Acrylic fallback require the Windows checks above.
  Automated mocks do not establish those platform behaviors.

## Code layout

- `main.py`: application startup and existing global hotkey wiring.
- `ui/popup.py`: glass UI, animation states, following, incremental text rendering.
- `ai/pipeline.py`: router → answer/screenshot/command orchestration and bounded context.
- `ai/worker.py`: Qt worker, cancellation, stale-update guards, safe shutdown.
- `ai/gemma.py`: reusable Ollama client, capabilities, content-only streaming.
- `router/router.py`: existing structured model router and declared command schemas.
- `integration/desktop.py`: GUI-thread screenshot/clipboard/confirmation bridge.
- `integration/actions.py`: validated Windows actions and verified keyboard targets.
- `integration/hotkey.py`: existing native Windows hotkey registration.
- `tests/`: mocked inference, action validation, UI animation and lifecycle checks.
