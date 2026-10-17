# Mote

Mote is a local Windows desktop assistant beside your cursor. Press
**Ctrl+Shift+Space**, ask a question, and receive a streamed Gemma response.
The router selects an answer, screen context, or up to eight Windows actions.
Explicit commands use a fast local parser; other wording uses the model.
Routing, inference, app discovery, and audio control run off the GUI thread.

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
server to launch. Python dependencies are PySide6, pypdf for PDF extraction,
and the Ollama Python client (which installs HTTPX and Pydantic), plus
Windows-only pycaw for audio control. Read-aloud support is included in PySide6;
voice input adds faster-whisper, sounddevice, and a compatible PyAV version
(all listed in requirements.txt).

The app starts hidden. Model loading happens on the first request, so the first
answer may take longer. Gemma is installed separately; Whisper downloads only
when voice recognition first needs it.

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
“Open example.org”, “Open Visual Studio Code”, “Open YouTube and set the volume to 50”,
“What can you do?”, or “Copy this text: Hello from Mote”.

## Voice input

Click the **microphone** to record and see live draft text, then click again to
stop and finalize the transcript. Draft updates are requested about every 1.2
seconds; actual latency depends on CPU speed and first-time model loading.
The compact status shows **Listening...** or **Transcribing...**, with a soft
accent on the microphone. A recording ends automatically after 60 seconds.
Nothing records when Mote opens or the microphone feature is inactive.

Recognition uses **faster-whisper / Whisper base.en**, entirely on the CPU with
int8 inference. `sounddevice` captures the default mono microphone; unsupported
16 kHz capture rates are resampled with PyAV. Starting capture stops read-aloud
playback, and the speaker is disabled until voice input finishes. No audio is
uploaded or permanently saved. The loaded recognition model is reused.

The first transcription downloads the model weights from Hugging Face, with a
visible first-use download status; this requires internet, but speech recognition
itself is local. Model files are cached under `~/.cache/mote/whisper`. Download,
voice, and microphone errors appear beside the input controls, with full details
in the status tooltip. This does not require an API key or a cloud speech API.

Draft text appears at the input cursor with spacing, keeping existing text and
attachments. Later updates replace only the provisional voice span. Typing
outside that span is preserved; editing the voice words yourself pauses automatic
replacement for that recording so your corrections are not overwritten. Stopping
refines the complete recording once. Review/edit, then press Enter to send;
transcription never submits automatically. Enter also accepts a current draft.
The first **Escape** cancels voice input, removes its untouched provisional draft,
and keeps Mote open (your manual edits remain);
otherwise Escape dismisses it. The existing **Ctrl+Shift+Space** toggle still
dismisses Mote immediately and cancels voice input. Ctrl+Shift alone has no action.

For slower systems, select the smaller English model before launching:

```powershell
$env:MOTE_STT_MODEL = "tiny.en"
.venv\Scripts\python.exe main.py
```

Only `base.en` and `tiny.en` are accepted. GPU recognition is deliberately not
enabled; CPU mode works without CUDA libraries. Capture stops promptly on
cancellation. Model loading/download and an in-progress inference call cancel
cooperatively; their late results are discarded. Quit keeps the event loop alive
until both the voice and AI workers finish, rather than destroying live threads.

## Read aloud and file attachments

The **speaker** in the response header reads the completed answer; click again
to stop. Speech is opt-in and uses Qt Text-to-Speech's native **SAPI** backend
on Windows, with installed offline Windows voices. It does not read loading
states, error messages, or model reasoning. Playback stops on dismissal, a new
request, or shutdown. If voices/audio are unavailable, the header shows an error
with details in its tooltip. No cloud speech API or per-sentence process is used.

Click the **paperclip** to select multiple files in the native file picker:

- Text/code/data: `.txt`, `.md`, `.log`, `.py`, `.js`, `.ts`, `.html`, `.css`,
  `.json`, `.csv`.
- Documents: `.pdf` with extractable text. Password-protected, scanned, and
  damaged PDFs produce an explanation; OCR is not included.
- Images: `.png`, `.jpg`, `.jpeg`, `.webp`. The selected Ollama model must
  advertise vision; otherwise the request fails clearly without image analysis.

Selected files appear as removable, horizontally scrollable chips above the
input. Following pauses while the picker is open. Chips stay during processing
and on failure/cancellation, and clear only after a successful response and
worker confirmation that their contents were captured. Files added while an
earlier request is running remain available for the next request.

Limits: **6 files**, **10 MB per file**, **25 MB combined**, and **20 megapixels
per image**. Text uses UTF-8/UTF-16 BOM handling with a Windows-1252 fallback.
Formatting is preserved. Each file contributes up to 16K characters, with a
shared 48K-character context budget; PDFs are limited to 50 pages. A visible
**File excerpts used** notice and request metadata identify truncation.

Extraction runs in the existing worker. A structured request keeps the user's
question separate from filenames, file types, excerpts, truncation flags, and
image bytes. The router sees only the question; requests with attachments always
use the answer path and cannot execute desktop commands. The model is instructed
to treat file content as untrusted reference data. Ollama receives image bytes
through its multimodal `images` field, combined with explicit screen context if
requested. Files/images are not retained in conversation history.

Selecting files does not upload them. **Send/Enter** sends prepared context to
the configured `MOTE_OLLAMA_HOST` (localhost by default); a remote endpoint, if
explicitly configured, receives that context. Existing Ollama/model availability
errors preserve the chips for retry. No separate future Gemma wiring is needed;
actual text/vision answers require a running Ollama server and a capable model.

## Screen context and model capabilities

The router requests screen context when needed. A second check catches explicit
references such as “on my screen” even if the classifier misses the image flag.
Supplied error/code text and screenshot how-to questions do not force capture. Mote
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
shell commands, Python code, or model-supplied executable paths/arguments.
A fixed PowerShell discovery script reads installed app names; user text is never
interpolated into that script.

| Command | Available behavior |
| --- | --- |
| `open_application` | Any discoverable installed app by name, using Start-menu shortcuts, Windows app IDs, and registry App Paths. Ambiguous names require clarification; portable apps need registration or a Start-menu shortcut. |
| `open_website` | Open any HTTP/HTTPS domain or URL in the default browser, including paths and queries. Common names such as YouTube are recognized. Unknown brand names require a full domain; other schemes and credentials are rejected. |
| `open_windows_settings` | Display, sound, Bluetooth, network, Wi-Fi, privacy, accessibility, Windows Update. |
| `copy_text` | Copy the supplied text to the Windows clipboard. |
| `press_hotkey` | Ctrl+C/V/Z/A/S, Alt+Tab/F4, Win+D/E; **requires explicit confirmation**, default Cancel. |
| `set_volume`, `adjust_volume` | Windows default audio output: set 0–100%, raise/lower by 5 percentage points, mute/unmute. Setting a positive volume or raising it also unmutes. |

Command plans run in the requested order. All command schemas and app lookups
are checked before the first action. If an action fails or a shortcut is declined,
remaining actions stop and the result reports earlier successes. Actions already
completed are not rolled back. A launch means Windows accepted an open request;
it does not wait for the app/site to finish loading. Conditional workflows and
requests combining unsupported actions are answered for clarification.

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
| `MOTE_STT_MODEL` | `base.en` | Local English voice model: `base.en` or `tiny.en`. |
| `MOTE_STT_CACHE` | `~/.cache/mote/whisper` | Download/cache directory for local recognition weights. |
| `MOTE_CONTEXT_LENGTH` | `8192` | Shared router/answer context size; 2048–131072. Smaller contexts reduce memory but can reject/truncate long requests. |
| `MOTE_GPU_LAYERS` | `auto` | Let Ollama choose GPU offload. Optional integer `num_gpu` override; `0` selects CPU, `999` requests all layers. Hardware/runtime support and enough memory are still required. |
| `MOTE_KEEP_ALIVE` | `30m` | Ollama residency duration. `-1` keeps the model loaded until Ollama evicts/stops it; `0` unloads after use. |

By default routing and answers use the same model with a 30-minute keep-alive,
without duplicate model instances or loading at startup. Choosing different
models may increase memory use and latency. Use local models and the local
endpoint to keep prompts and screenshots on your machine.

## Router speed, preloading, and VRAM

Complete commands such as “open YouTube and set the volume to 50” skip model
inference entirely. Other requests use a compact structured router prompt with
thinking disabled. Automatic GPU offload is explicitly requested (`num_gpu=-1`).
Routing and answering share context/GPU settings so switching
between them does not request a different runner configuration. The application
does not download Gemma models or require inference for supported explicit commands.

Use native Ollama on Windows with supported GPU drivers. Ollama automatically
places models on GPU where possible; an oversized model/context can spill to CPU
and system RAM. Keeping a model loaded does not itself force GPU residency.
See [Ollama hardware support](https://docs.ollama.com/gpu) and
[residency/context guidance](https://docs.ollama.com/faq).

Preload the configured model(s), check vision support, and inspect the actual
GPU residency reported by `/api/ps`:

```powershell
.venv\Scripts\python.exe scripts/model_status.py --warmup
ollama ps
```

The diagnostic prints `gpu_percent`, `vram_bytes`, model capabilities, context
settings, and keep-alive. `100% GPU` in `ollama ps` confirms full placement. If
placement is mixed, close other GPU workloads or use a smaller model/context.
Leave `MOTE_GPU_LAYERS=auto` initially; forcing layers beyond available memory
can fail. Apple Silicon uses unified memory, so it cannot verify Windows VRAM.

## Preview and automated tests

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

Run a live router-only evaluation (no desktop commands or screenshots execute):

```powershell
.venv\Scripts\python.exe scripts/evaluate_router.py --output router-results.json
.venv\Scripts\python.exe scripts/evaluate_router.py --model-only --output classifier-results.json
```

Each case prints pass/fail and latency; the JSON contains the complete decisions.
The first command measures deployed routing including fast paths. `--model-only`
disables command fast paths to exercise Ollama on every case. This requires the
configured router model to be installed and Ollama running. Mocked tests establish
code behavior, not the accuracy of an installed model.

See [TEST_FINDINGS.md](TEST_FINDINGS.md) for the baseline, each test iteration,
measured fast-path timings, and platform limitations.

The answer model receives Mote's identity, tool registry, platform availability,
vision support, current screenshot state, memory limits, and unsupported features
on every answer. It can describe app tools without pretending this answer stage
can execute them or that it can see a screen without an attached image.

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
4. Ask to open Notepad, an app outside the old list (Firefox/VS Code/Word),
   a Store app, an arbitrary domain/path, and Wi-Fi settings. Verify each target.
   Try an ambiguous app name and an uninstalled app. Copy supplied text and paste
   it manually. Request “open YouTube and set the volume to 50”; verify order,
   browser launch, and the volume slider. Test mute/unmute and relative volume.
   Confirm the popup remains responsive during initial app discovery. Ask “What
   can you do?” and verify the answer describes the available tools accurately.
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
11. Click the paperclip, select several supported files, remove a chip, and
    check that the picker does not hide Mote or move it. Submit code/PDF context
    and verify a real model answer uses its contents. Try long text, a scanned
    PDF, and an oversized/unsupported file. Check retry preserves attachments.
12. With a vision model, attach an image and ask a specific visual question.
    With a text-only model, verify the capability error. Test the speaker's
    audible output, stopping, completion, new-request cancellation, and dismissal
    with the installed Windows voice and actual audio device.
13. Click the microphone, speak, then stop. Verify accurate text appears at the
    caret without overwriting typed text or submitting. Repeat with attachments,
    after playing TTS, and with the default microphone at 44.1/48 kHz if available.
    Test silence, denied permissions, and an unplugged device. Escape should
    cancel voice input first; dismissal and quit must stop the recording. Rapidly
    click start/stop/cancel and confirm stale transcripts never appear. Check
    listening/progress feedback and reduced-motion mode without a layout change.
    Verify draft text appears while still recording, is revised without duplicate
    phrases, preserves edits outside it, and refines on Stop. Try correcting the
    draft manually and canceling after several live updates.

## Scope and limitations

- Arbitrary shell commands, general mouse automation, and
  automatic website reading/search are not implemented.
- Installed apps must be discoverable through Windows registration or Start-menu
  shortcuts. App discovery is cached for 60 seconds. Use the full app name if
  several installed apps match.
- Plans contain at most eight supported commands, execute sequentially, and stop
  at the first failure/declined shortcut. They do not wait for app load completion.
- OCR, arbitrary shell commands, general mouse automation, and automatic website
  reading/search are not implemented.
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
- `services/attachments.py`: file validation, bounded local extraction, structured context.
- `services/tts.py`: lazy offline speech engine and playback lifecycle.
- `services/stt.py`: bounded in-memory audio capture, resampling, cached Whisper, cancellation.
- `router/router.py`: structured router, command-plan validation, and declared schemas.
- `router/intents.py`, `router/urls.py`: explicit-command fast paths, screen checks, URL normalization.
- `ai/capabilities.py`: answer-model identity and session-aware capability instructions.
- `ai/runtime.py`, `ai/diagnostics.py`: shared model settings and GPU/vision diagnostics.
- `integration/apps.py`, `integration/audio.py`: installed-app discovery and Core Audio volume.
- `scripts/`: live router evaluation and optional model preloading/status.
- `integration/desktop.py`: GUI-thread screenshot/clipboard/confirmation bridge.
- `integration/actions.py`: validated Windows actions and verified keyboard targets.
- `integration/hotkey.py`: existing native Windows hotkey registration.
- `tests/`: mocked inference, action validation, UI animation and lifecycle checks.
