# Mote Windows release

## Build the installer (Windows x64, Python 3.11 recommended)

Install [Python](https://www.python.org/downloads/windows/) and
[Inno Setup 6](https://jrsoftware.org/isdl.php), then run from the checkout:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1
```

The script creates a separate build venv, installs the application and build
requirements, executes the test suite and compilation, builds an onedir
windowed executable, executes its dependency/GUI smoke check, and compiles
`dist\installer\MoteSetup.exe`. Failure stops the build. It also records resolved
Python dependency versions and prints the installer SHA-256. Build the same
source revision and use those recorded versions for an exact dependency repeat.

Alternatively push these files and run **Actions → Windows installer → Run
workflow** on GitHub. A `v*` tag also triggers it. Download the
`Mote-Windows-installer` artifact after a successful run. The workflow needs to
exist on the selected remote branch; local uncommitted files are not available
to Actions. The workflow does not publish a public release automatically.

This is a per-user installer with Start Menu entry, optional desktop shortcut,
Python/runtime dependencies, application icon, and standard Windows uninstall.
No admin rights are required. The executable is windowed (no console). The
Start Menu shortcut and process share a stable AppUserModelID. Pin the installed
shortcut through Windows' normal pinning interface. Mote's floating tool window
itself intentionally stays out of the normal application taskbar list.

The icon source is `assets/mote.svg`; `scripts/create_icon.py` creates the ICO
with 16, 24, 32, 48, 64, 128 and 256px PNG frames. Packaging follows the
[PyInstaller spec workflow](https://pyinstaller.org/en/stable/spec-files.html).

## Runtime setup

The installer does **not** bundle Ollama, Gemma, or a Whisper model. Install and
start [Ollama for Windows](https://ollama.com/download/windows), then run:

```powershell
ollama pull gemma4:e2b
```

First launch opens Settings. Refresh queries `/api/tags` and each model's
`/api/show`, without inference or downloading models. Answer selections require
advertised `vision` plus verified total parameters of at most 6 billion.
Parameter metadata takes priority over the descriptive parameter label; model
file/download size is never used. Gemma 4 E4B is excluded (about 8B total).
Unknown counts, text-only Phi 3.5, and 11B Llama vision are excluded. Independently
verified small Phi/Llama vision models can appear; no fictional variants are
added. Some local E2B installations lack vision. If so, install `gemma3:4b` or
another eligible model, Refresh, and choose Use model. The current unavailable
model is explained in Settings. No model download is automatic.

The router retains `MOTE_ROUTER_MODEL` / Gemma 4 E2B for structured decisions.
An answer change applies between requests; the old request finishes safely and
the next queued/new request receives a fresh backend/capability cache. It does
not mutate the backend in an active worker. Capability instructions use that
new backend automatically.

First microphone use may download the configured small Whisper model. Subsequent
transcription is local. Read aloud requires an installed offline Windows SAPI
voice. The package includes these dependencies, not prerecorded models or a
bundled external model server. GPU/CUDA speech acceleration is not required;
Whisper uses CPU int8. Linux microphone use separately requires system PortAudio
(e.g. `libportaudio2`); this isn't necessary for UI preview.

## Settings and recovery

Use the toolbar gear or tray Settings. The tray also offers Open and Quit.
Escape dismisses; Ctrl+Q quits. Follow-cursor, glass opacity (60–100% of the
original glass alpha), model, and shortcut persist through Qt's per-user INI
settings (`Mote/Mote.ini` under the user's Qt configuration directory).
Saved model selection takes precedence over `MOTE_MODEL`, which supplies the
initial default when no model has been saved. Other existing runtime environment
variables remain supported. User data is not stored inside Program Files.

Glass opacity changes only the input/response surface alpha. Opening/closing and
screenshot capture keep owning temporary whole-window opacity, so they do not
reset the selected glass. Both primary corners use the shared 28px radius.
Settings keeps its own readable opacity and scrolls on short/high-DPI screens.

Record a single shortcut with Ctrl, Alt, or Win and a nonmodifier key. Mote
rejects unsupported keys and its own Ctrl+Q. It registers a new Windows hotkey
before releasing the old one; a conflict leaves the old one working. Preview
can save a shortcut but cannot verify Windows registration. If a saved binding
conflicts at startup, Mote tries its default and leaves tray/settings accessible
so the user can recover. Do not run multiple Mote instances.

## Native release verification (required before distribution)

A green build/smoke test does not verify microphone devices, compositor pixels,
or installation in an interactive Windows session. On a clean Windows 10/11 x64
machine, complete these checks:

1. Install `MoteSetup.exe` as a standard user. Launch via Start Menu and verify
   the icon, tray, no console, Open/Settings/Quit actions, and normal taskbar
   pinning of the installed shortcut. Quit/restart and confirm hotkey release.
2. With Ollama absent/stopped, confirm first-run setup gives the official link
   and pull command. Install/start it and Refresh without restarting Mote.
3. Confirm only eligible installed models appear. Verify E4B/11B/text-only and
   unknown-metadata exclusions. Change the model while a response is active;
   verify that response finishes and the next request uses the chosen model.
   Restart and confirm persistence. Remove a selected model and check the error.
4. Record a different hotkey and toggle the popup. Reserve the same shortcut in
   another application; reassignment must fail and leave the original working.
   Test modifier-only input, Esc, and Ctrl+Q.
5. Drag glass opacity and restart. Test opening, dismissal, current-screen
   capture, and capture cancellation: the selected surface alpha must remain.
   Inspect both 28px corners at 100/150/200% scaling and short display heights.
6. Generate streamed text, follow-up context, and a screenshot answer with the
   chosen vision model. Confirm the correct monitor and exclusion of Mote plus
   Settings. Cancel/reopen/submit quickly and check stale output is suppressed.
7. Test following/pinning at edges and across mixed-DPI monitors, reduced motion,
   Copy into another app, long scrollable replies, attachment picker/PDF/image
   processing, microphone record/stop/cancel/transcript, and Read aloud/Stop.
8. Open an application/site, change volume, and confirm/cancel a keyboard tool
   against a disposable target. Do not infer success from a mock test alone.
9. Quit during inference, recording, transcription, and model refresh; check no
   QThread destruction warnings or lingering Mote process. Reopen after quit.
10. Uninstall through Windows Settings; confirm executable and Start Menu entry
    are removed. Per-user preferences/Whisper cache may remain for reinstalls.

## Verification in this development session

The available host is Linux. No Windows executable or installer has been built
or installed here, and the GitHub workflow has not been dispatched from this
checkout. Native verification above remains outstanding. The Linux full-runtime
smoke check exposed missing system PortAudio; ordinary UI preview and mocked
STT tests do not require a microphone backend. Windows wheels supply PortAudio,
and the native packaged smoke check deliberately fails if it cannot load.

The release is unsigned; Windows may show publisher/SmartScreen prompts.
Signing and model downloads are outside this installer. No services, database,
cloud infrastructure, or bundled large models are introduced.

Automated result for this source revision: **165 tests passed** using
`python -m unittest discover -s tests -v` (about 5 seconds). Python compilation,
Ruff checks on changed Python files, and `git diff --check` passed. Coverage
includes model metadata exclusions, persistence, surface-only transparency,
deferred switching/cache refresh, hotkey conflict preservation, UI lifecycle,
existing STT/attachments/tools/capture tests, and packaged import/icon contracts.
This is automated/mock coverage, not native Windows certification.
