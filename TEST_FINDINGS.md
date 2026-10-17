# Tool-use upgrade findings — October 17, 2026

## Test history

Tests ran on macOS with Python 3.13 in a temporary virtual environment. Qt used
its offscreen backend. Model responses, desktop capture, and Windows APIs were
mocked; no real applications were opened or keys injected.

| Run | Result | Findings and follow-up |
| --- | --- | --- |
| Router baseline, before implementation | 15 tests; 9 passed, 4 failures, 2 errors | Confirmed fixed app enum, unsupported multi-command output, absent inference-free paths, and missed screen flag. |
| Router after changes | 15/15 passed | Arbitrary app display names, plans, explicit-command fast paths, and screen fallback accepted. |
| Full suite 1 | 58 tests; 3 failures, 1 error | Updated old fixed-list/unsupported-volume/single-dispatch expectations and normalized built-in app casing. The error was a subtest within the obsolete arbitrary.exe expectation. |
| Full suite 2 | 77/78 passed | New URL test caught a trailing newline stripped before validation. Changed validation to reject controls first. |
| Full suite 3 | 78/78 passed | Pipeline, audio, catalog, screenshot, UI, and lifecycle tests passed. |
| Full suite 4 | 86/87 passed | Real-client transport fixture lacked Ollama's required model_info field. Corrected the mock response. |
| Full suite 5 | 87/87 passed | Real Ollama client serialized screenshot bytes as base64 in outgoing chat JSON. |
| Full suite 6 | 90/90 passed | Office/browser name variants and explicit automatic GPU offload/CPU override passed. |
| Final full suite | 92/92 passed, about 3.2 seconds | Added current-screen troubleshooting and domain-like app-name disambiguation checks. |

Python compilation and `git diff --check` also passed.

## Established behavior

- Router accepts any application display name; the executor looks it up in a
  Windows catalog of Start-menu shortcuts, Get-StartApps IDs, and App Paths.
  Common aliases work in either naming direction; ambiguous matches require a
  fuller name. Launch destinations come from local discovery, not model paths.
- HTTP/HTTPS domains and URLs accept paths, queries, IDN hostnames, IP addresses,
  and localhost. Other schemes, embedded credentials, malformed ports/hosts,
  backslashes, whitespace, and control characters are rejected. Common website
  names have explicit mappings; unfamiliar website brands need a full domain.
- Up to eight commands are validated and preflighted before execution, then run
  in order. Failure or declined shortcut stops later actions; earlier successes
  are reported. Completed actions cannot be rolled back.
- Core Audio implementation supports absolute percentage, relative 5-point
  adjustments, mute/unmute, clamping, and unmuting on positive absolute volume
  or volume increase. Tests verify API calls with a mocked endpoint.
- Screen references can correct an answer-route classifier miss. Tests cover
  screenshot request, GUI capture exclusion/restoration, resizing, attachment,
  actual Ollama client JSON/base64 serialization, and absence from text memory.
  A vision-incapable model produces an error instead of silently omitting images.
- Answer instructions include Mote's identity, tool registry, platform, image
  capability/current attachment state, memory limits, and unsupported features.
  Instruction tests do not establish how faithfully a real model follows them.
- Non-GUI actions and discovery remain on the worker; clipboard, screenshot,
  and shortcut confirmation remain on the GUI thread. Existing stale-result,
  cancellation, shutdown, animation, and startup checks pass.

## Speed and GPU

Four explicit-command evaluation cases passed with inference prohibited:
VS Code, Thunderbird, a domain/path/query, and YouTube plus volume. Observed
routing times in this small local sample were approximately 0.1–1.4 ms. These
numbers exclude Windows discovery, launch, and audio calls; they are not a live
model benchmark or a before/after inference comparison.

Other wording uses a compact structured model prompt. Router thinking stays off.
Router and answer calls share context, GPU-offload, and keep-alive settings.
The default explicitly requests Ollama dynamic GPU offload (`num_gpu=-1`).
`MOTE_GPU_LAYERS` can override that; `MOTE_CONTEXT_LENGTH` controls memory demand.
`MOTE_KEEP_ALIVE` controls residency duration, not which memory is used.

The unrestricted localhost diagnostic returned connection refused. No live
classifier, real Gemma answer, real image interpretation, or GPU residency could
be tested here. Full Windows VRAM placement requires compatible hardware/drivers
and sufficient free memory. It cannot be guaranteed by this application.

## Windows handoff

Install the updated dependencies, including the Windows-only pycaw package:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts/model_status.py --warmup
ollama ps
.venv\Scripts\python.exe scripts/evaluate_router.py --output router-results.json
.venv\Scripts\python.exe scripts/evaluate_router.py --model-only --output classifier-results.json
.venv\Scripts\python.exe main.py
```

The evaluation scripts never execute the router's actions or capture screens.
`--model-only` disables command fast paths so all cases call Ollama. Diagnostics
report image capabilities and actual loaded model/VRAM bytes. `100% GPU` in
`ollama ps` confirms full GPU placement. Screen analysis requires `vision` in the
answer model's capabilities; select a suitable `MOTE_MODEL` if absent.

Manually test app discovery/launch (including a Store app and an app outside the
old list), an arbitrary website, YouTube plus volume 50, mute/unmute, screenshot
analysis on a second monitor, and the capability answer. Also verify shortcut
confirmation/focus restoration, native capture exclusion, and GPU residency.
Use the expanded Windows checklist in README.md for the existing UI behaviors.

App discovery is cached for 60 seconds. Unregistered portable apps need a Start-
menu shortcut. Domain-like app names can be disambiguated with “Open the
application Paint.NET”. Launch success means Windows accepted a request, not that
the application or website finished loading. General browser reading/search,
mouse automation, arbitrary shell commands, microphone, and file attachments
remain outside the implemented tool set.
