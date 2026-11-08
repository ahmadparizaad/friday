# Friday skills plan

Status: plan only, written 2026-10-02. Companion to `docs/PLAN-M6-M9.md`. Read that first for the planner, perception, voice and safety decisions; this file covers only skills.

## Goal

Cover the repetitive tasks people do every day with **deterministic skills**, so the on-device planner (Qwen3-4B-Instruct-2507, 8/12 correct and 2/3 injections followed in our benchmark) is a rare fallback instead of the main path. Everything runs locally. No cloud backend, no API keys, no telemetry leaving the machine.

## Decisions already made (do not reopen)

| Area | Decision |
|---|---|
| Cloud | **Fully local.** No optional cloud backend. Cloud-based computer-use models are used only as an offline benchmark comparison, if at all, never in the product. |
| Order of attempts | skill → AX-driven planner step → VLM fallback. A skill is always preferred when one matches. |
| Routing | laya-mlx picks the skill (fixed options, zero generation). Slot filling is deterministic first; a constrained local model fills only what extractors cannot. |
| Safety | Every skill declares an `action_class` and goes through the existing policy gate. Floors stay: destructive and outward-facing always confirm; shell, payments, keychain, System Settings always refuse. |
| Models | Planner and VLM as in the main plan. Apple's on-device model is optional for slot filling and the goal-done check only. |

## What a skill is

A skill is a small, tested, versioned package with a fixed contract. It is *not* a prompt.

```
skills/<name>/
  skill.json        manifest (below)
  run.py (or .swift) the implementation; calls a backend, never a shell string
  tests/            unit tests with fixtures + a dry-run golden test
  examples.jsonl    10-30 utterances per skill, used to train/evaluate the laya route
```

Manifest fields:

| Field | Meaning |
|---|---|
| `id`, `version` | Stable id (`mail.compose_with_attachment`) and semver. |
| `examples` | Pointer to utterances for routing (see `docs/ROUTING.md`). |
| `slots` | Typed list. Each slot has a type (`file`, `contact`, `app`, `duration`, `text`, `enum`) and a **provenance rule**: literal span of the utterance, an id from a resolver (Spotlight/Contacts/Calendar), or a fixed enum. No free text reaches a backend unless the slot type is `text` and the skill treats it as data. |
| `action_class` | One of the existing classes (`read`, `navigate`, `mutate_local`, `destructive`, `outward_facing`). Highest class any step can reach. |
| `requires` | Apps, binaries, permissions (Automation for Mail, Full Disk, etc.). Checked before running, with a clear "missing X" message. |
| `steps` | Ordered backend calls. Each step has a `dry_run` description shown before anything happens. |
| `postcondition` | Machine-checkable end state (file exists, mail draft has attachment, event is on the calendar). Failing it fails the skill. |
| `undo` | How to reverse, or `none` (which forces `confirm`). |
| `resources` | CPU/GPU/memory hints, so the loop can unload models before a heavy job. |

### Backend preference order

1. **App Intents / Shortcuts** (`shortcuts run`) when the app exposes them.
2. **AppleScript / JXA** for scriptable apps (Mail, Calendar, Notes, Reminders, Finder, Music, Safari, Numbers, Keynote).
3. **System tools and CLIs** called with an argv array and allow-listed binaries: `mdfind`, `sips`, `ffmpeg`, `ffprobe`, `qpdf`, `pdfunite`, `zip`, `open`, `osascript`, `defaults` (read-only keys only).
4. **URL schemes and `mailto:`**.
5. **Chrome DevTools Protocol** (already in the registry as `browser_*`).
6. **AX click/type** (the M6 tools), only when nothing above exists.

Rules: no `shell=True`, no string-built commands, paths resolved and checked against an allowed-directory list, file names passed as arguments never interpolated. Deleting means moving to the Bin, never `rm`.

## Choosing which skills to build

Score each candidate 1-5 on: **frequency** (how often people do it), **determinism** (a fixed procedure works), **API availability** (a scriptable surface exists), **verifiability** (we can check the end state), and **risk** (lower is better). Build in descending order of the product of the first four, with risk used as a tiebreaker and to set the confirm policy.

### Tier A: build first (high frequency, scriptable, easy to verify)

| Area | Skills | Backend |
|---|---|---|
| Files | find file by name/type/date, open it, reveal in Finder, move/copy/rename, zip/unzip, tag, clean old Downloads (move to Bin after confirm), create folder | `mdfind`, Finder AppleScript, `zip` |
| Mail | compose with recipient + subject + body + attachment (stops at send confirm), search mail, mark read | Mail AppleScript, `mailto:` |
| Calendar and reminders | what's next, create event, create reminder, create note | Calendar/Reminders/Notes AppleScript |
| Messages | draft a message (never auto-send) | URL scheme / AppleScript |
| System | volume, mute, brightness, dark mode, Do Not Disturb, lock, screenshot (region/window/screen), sleep display, open app, quit app, switch to app | existing registry tools + AppleScript/`defaults` |
| Web | open URL, search, new tab, close tab, read page title/text, download | `open`, CDP |
| Media | play/pause/next, set volume, play playlist | Music / Spotify scripting |
| Windows | tile, maximize, move to display, minimize others | AppleScript/AX (low risk) |
| Time | timer, stopwatch, alarm | Shortcuts/Clock URL or own timer service |
| Clipboard | copy selection, read clipboard aloud, paste as plain text | AX/`pbcopy`/`pbpaste` |

### Tier B: document and media utilities (scriptable via CLI, still easy to verify)

| Area | Skills | Backend |
|---|---|---|
| PDF | merge, split, extract pages, compress, rotate, extract text (OCR with Vision) | `qpdf`/PDFKit, Vision |
| Images | resize, convert format, strip metadata, make thumbnail, batch rename | `sips`, ImageIO |
| Audio and video basics | convert, trim, concatenate, extract audio, compress, change speed, burn in subtitles | `ffmpeg` |
| Transcription | transcribe a recording or video, save as txt/srt, summarize with the local model | local Whisper (MLX) + planner model |
| Screen | start/stop screen recording, save clip to folder | `screencapture -v` |
| Text | rewrite/fix/shorten selected text, translate, summarize a file | local model on selected text; result goes to clipboard or a new note, never overwrites silently |

### Tier C: creative apps (not simple, so scoped carefully)

The honest finding: driving a creative app's GUI with accessibility clicks is the *wrong* approach. These apps are large, canvas-based and expose little AX. Use their scripting surfaces instead, and accept that Friday covers **templated operations**, not open-ended creative work.

**Video editing**

| Level | Scope | Feasibility |
|---|---|---|
| C1: file-level (no editor GUI) | trim by timestamps, cut silence, join clips, add title card, add subtitles, resize for a platform (vertical, square), normalize audio, make a GIF | High. `ffmpeg` plus local Whisper for subtitles and silence detection. This covers most "daily repetitive" video chores for creators. |
| C2: editor automation | open project, import media, add to timeline, export with a preset | Medium. DaVinci Resolve has an official Python API; Final Cut works through FCPXML import/export; Premiere through UXP/ExtendScript; CapCut and iMovie have no usable scripting. Pick **one** (Resolve is the best documented) and gate it behind detection. |
| C3: creative decisions | "make it feel punchier", pick the best take | Not a skill. Out of scope for the first year; planner fallback would fail and should say so. |

**Blender / 3D**

| Level | Scope | Feasibility |
|---|---|---|
| C1: headless utility (`blender -b -P script.py`) | import/convert models (glb, fbx, obj, stl), render a still or turntable from a file, change render settings, batch render a folder, apply a decimate/scale/center, export thumbnails, run a named preset scene | High. Parametrized, tested `bpy` scripts with typed slots. Verifiable by checking output files. |
| C2: in-app assistance on an open scene | add a primitive, set material color, move the camera, apply a modifier through the Python console or a small add-on socket | Medium. Needs a Blender add-on that listens locally. The operation list is a fixed enum, so it stays gate-able. |
| C3: freeform modeling or animation | "model a chair", "animate this character" | Not a skill. Needs real 3D reasoning; the on-device planner cannot do it. Say so rather than attempting it. |

The rule for Tier C: ship C1 first because it reuses the CLI backend and the same safety model as Tier B. Only start C2 if users actually ask for it, and record the demand (see "Skill discovery").

### Tier D: later, only if demand is shown

Spreadsheets (Numbers/Excel: sort, sum a column, export CSV), presentations (Keynote: new deck from outline), expense or receipt handling from photos/PDFs (local OCR), code-editor chores (open project, run tests), password-manager flows (stay refused by policy), smart-home (HomeKit Shortcuts).

## Skill discovery: finding what people actually repeat

Do not guess. Use three sources, all local:

1. **The user's own week.** A short interview/checklist before Tier A is frozen: ask the user to list what they did repeatedly in the last week. This is the very first input; the current list above is a starting hypothesis, not data.
2. **Friday's audit log (planner-to-skill mining).** Any goal that the planner solves goes through the hash-chained audit log. A background job clusters successful runs by utterance and step pattern. When the same pattern shows up N times (default 3), it produces a **candidate skill draft** (manifest + steps from the trace). A human reviews, parametrizes and tests it before it is enabled. This turns the planner into a skill generator and shrinks the fallback over time. Everything stays on the machine.
3. **Public demand proxies, read once.** Apple Shortcuts gallery categories, the most-installed Raycast/Alfred workflows, and App Store "most used automations" lists, to sanity-check the Tier A/B lists. Treat them as hints only.

## Routing and slot filling

- **Router.** laya-mlx classifies the utterance into one skill id plus "none" (none → planner). Each skill contributes its `examples.jsonl` to the route set. Hold out 20% as a test set; target at least 95% top-1 accuracy on in-scope utterances and a measured false-route rate on out-of-scope ones (a wrong skill is worse than no skill).
- **Slots.** Extractors first (dates, durations, numbers, app names, file types), resolvers for entities (Spotlight for files, Contacts for people, Calendar for events), then a constrained local model only for slots left unfilled. If a slot is still ambiguous, ask a short spoken question; do not guess.
- **Ambiguity.** Multiple files matching "the invoice" → read the top 3 names aloud and ask. This is a `read` action so no gate floor is hit.
- **Provenance.** Each slot value carries its source (`utterance`, `resolver`, `enum`). The gate refuses a step whose argument has no provenance. This is the same structural injection defense as the planner.

## Safety model for skills

- Every skill declares its highest `action_class`; the gate applies it before the first step runs, not per step at runtime.
- Dry-run is mandatory in tests and available to the user ("what would you do?"). The dry run is spoken/shown from the manifest, so it needs no model.
- Destructive operations move to the Bin, keep a restore record, and always confirm. Outward-facing steps (send, post, upload, share) always confirm and need a physical click or passphrase, never spoken approval alone.
- Allowed-directory list per skill (default: Home, Desktop, Documents, Downloads, and the folder the user names). System, Library, keychain and other users' files are never in scope.
- Binary allow-list and fixed argument templates for CLI skills; user text enters only as an argument value, never as flags (reject values starting with `-` unless the slot type allows it).
- Long jobs (ffmpeg, Blender renders) run as supervised subprocesses: progress, cancel on voice or notch, hard timeout, output written to a temp file then moved into place.
- Resource handling: the loop unloads the planner and VLM before a heavy job when the profile says memory is tight (16 GB tier), and reloads after.
- Permissions: the first run of a skill that needs Automation/Accessibility/Files prompts the user once with a plain explanation, and records the grant in the profile store. Missing permission is a clean failure, not a retry loop.

## Workstreams

Numbered S1-S10; they run alongside M7-M9 of the main plan. Failing test first on each.

| # | Workstream | Deliverable | Acceptance |
|---|---|---|---|
| S1 | Skill runtime | Manifest schema + validator, loader, runner with dry-run, postcondition check, undo records, audit-log entries | Schema rejects missing `action_class`/provenance; dry-run touches nothing (asserted by a filesystem snapshot test); a failing postcondition fails the skill. |
| S2 | Backends | Typed wrappers for AppleScript/JXA, Shortcuts, allow-listed CLI runner, URL schemes, with argv-only invocation | Property tests: no shell metacharacter in a slot value can change the command; paths outside the allowed directories are refused. |
| S3 | Routing | `examples.jsonl` format, laya route-set builder, evaluation harness with held-out and out-of-scope sets | ≥95% top-1 in scope; reported false-route rate out of scope; "none" correctly hands off to the planner. |
| S4 | Slot resolvers | Spotlight file resolver, Contacts, Calendar, app resolver, date/duration extractors, ambiguity prompt | Table-driven tests incl. ambiguous names; resolver results always carry provenance. |
| S5 | Tier A skills | The Tier A list, each with manifest, tests, examples | 100% of Tier A skills pass dry-run and live tests on the dev machine; each has a machine-checkable postcondition. |
| S6 | Tier B skills | PDF, images, ffmpeg basics, transcription, screen recording, text transforms | Golden-file tests on fixture inputs; output files verified (duration, page count, dimensions). |
| S7 | Tier C1 skills | ffmpeg-based video chores and headless Blender utility skills | Fixture-based golden tests; renders compared by size/frame count (not pixel equality); missing Blender/ffmpeg detected with an install hint, nothing silently bundled. |
| S8 | Skill mining | Audit-log clustering → candidate draft generator → review CLI | A synthetic log with 3 repeated goals yields exactly one candidate with correct slots; nothing is auto-enabled. |
| S9 | Task-suite integration | Tag each M9 goal with the skill (or `planner`) expected to solve it; report skill coverage and planner-fallback rate | Suite reports `% goals solved by skills`, `% by planner`, success and safety violations per route. |
| S10 | Tier C2 (conditional) | One editor integration (suggest Resolve) or a Blender add-on, only after demand is recorded | Start only if mining or the user list shows repeated demand; same acceptance style as S6/S7. |

Order: S1 → S2 → (S3, S4 in parallel) → S5 → S6 → S7 → S9 → S8 → S10.

## Metrics and exit criteria

- **Skill coverage:** share of the ~50 suite goals solved without the planner. Target ≥70% by the end of S6, ≥80% after S7 for the goals that fall in Tier A-C1.
- **Skill success rate:** ≥95% on the live suite for each shipped skill; any skill below 90% is disabled until fixed.
- **Safety violations:** zero. Any forbidden side effect (wrong file moved, mail sent without confirm) fails the build.
- **Latency:** a skill-routed goal reaches its first action in under 1.5 s on the 16 GB profile (router plus slot filling, no generation).
- **Planner fallback rate:** tracked per week from the audit log; the target is a steady decrease as mined skills are added.

## Risks

| Risk | Mitigation |
|---|---|
| App updates break AppleScript dictionaries or menu layouts | Each skill has a live test run in CI-on-the-dev-machine; a version-probe step in the manifest; fail clearly, never half-complete. |
| Permission prompts (Automation per app) frighten users or loop | One explained prompt per skill/app, recorded; missing grant is a clean failure. |
| Wrong skill chosen from an ambiguous utterance | Held-out and out-of-scope evaluation; ask a short clarifying question; destructive skills always confirm. |
| ffmpeg/Blender not installed or wrong version | Detect and report with an install hint; do not bundle at first; pin tested versions in the manifest. |
| Heavy jobs starve the models on 16 GB | `resources` hints plus model unload/reload; supervised subprocesses with a timeout and cancel. |
| Mined skill drafts encode a one-off or unsafe sequence | Never auto-enable; human review; reuse the same tests and gate as hand-written skills. |
| Scope creep into creative work | Tier C3 is explicitly out of scope; the planner says it cannot do it rather than trying. |

## Open questions for the user

1. **The typical-day list.** What did you do repeatedly last week? This is the input that decides the first ten skills; the tiers above are a hypothesis.
2. **Video editor.** Which editor do you actually use (Final Cut, Premiere, DaVinci Resolve, CapCut)? Only one is worth automating (S10), and if the answer is CapCut or iMovie, stay with ffmpeg-based C1.
3. **Blender.** Is the need rendering/exporting existing scenes (C1, feasible) or building scenes by voice (C2/C3, mostly not)?
4. **Bundling.** OK to require `brew install ffmpeg qpdf` and a Blender install, with Friday detecting them, rather than bundling binaries?
5. **Languages.** Spoken languages to support for skill utterances (affects `examples.jsonl` and the router).

## Changelog

- 2026-10-02: plan created. Cloud option removed (fully local).
