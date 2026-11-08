# Friday M6–M9: from command router to goal-completing agent

Status: agreed direction, 2026-09-30. Source of truth for parallel work. Every workstream below is written to be picked up by one agent without reading the others; the **Contracts** section is the only shared surface.

## Goal

Friday is controlled by voice, sees the screen, operates the Mac without keyboard or trackpad, and completes a multi-step goal, all on-device. It is the first product built on a framework that replaces cloud computer-use models. The SDK is extracted after Friday works end to end (not before).

**Milestone demo goal:** "Find the invoice PDF in Downloads and attach it to a new mail to X." It spans Finder and Mail, needs perception, planning and verification, and ends at an outward-facing confirm.

## Decisions already made (do not reopen)

| Area | Decision |
|---|---|
| Planning | A local generative planner proposes one step at a time. Every step goes through laya-mlx routing and the policy gate. The planner can only name tools in the registry. |
| Safety claim | "No generation" now applies to the trusted path (router, gate, executor). The planner generates, and its output is untrusted until gated. README wording must change. |
| Perception | Accessibility (AX) tree first. Screenshot VLM only when the AX tree is missing or poor. |
| Hardware | Tiered by RAM through the existing profile store. 16 GB M4 Air is the lowest supported tier. |
| Voice | On-device wake word. Spoken approval only for low-risk, reversible actions. Destructive or outward-facing actions need a physical click or a spoken passphrase challenge. The notch UI has a mic on/off toggle the user can flip at any time; off means no audio is captured. |
| Verification | Per-step postcondition check, then a final goal check by VLM. |
| Benchmark | Own macOS task suite of about 50 goals, scored on success and safety violations. |
| Framework | Product first, SDK extracted after M8. |

## Architecture

```
mic → wake word → STT → utterance
                          │
                   goal loop (new) ───────────────────────────┐
   perceive (AX → VLM fallback) → planner proposes 1 step     │
                          │                                    │
                  laya router: validate tool + args            │
                          │                                    │
                     policy gate (floors unchanged)            │
                          │                                    │
                       executor → postcondition check ─ fail → replan / retry (bounded)
                          │                                    │
                  audit log (hash chain)  ── done? final VLM check ── finish / ask_user
```

Untrusted inputs: everything read from the screen and everything the planner emits. Trusted: the registry, the gate, the audit chain.

## Contracts (shared surface)

Agents implementing different workstreams code to these. Changing one needs an entry in the changelog section below.

- **`Observation`**: `{app, window_title, elements: [{id, role, label, value, bbox}], source: "ax"|"vlm", screenshot_path?}`. Element `id` is stable within one observation only.
- **`PlanStep`**: `{tool, args, expect}` where `expect` is a postcondition (see `Postcondition`). `tool` must exist in `tools.json`.
- **`Postcondition`**: a small declarative check, e.g. `{"element_appears": {"label": "Message sent"}}`, `{"app_frontmost": "Mail"}`, `{"element_value": {"id_label": "To", "contains": "bob@example.com"}}`. Evaluated against the next `Observation`; a VLM-judged form exists as fallback.
- **`GoalRun`**: `{goal, steps[], status: "running"|"done"|"failed"|"needs_user", budget}`. Budget bounds step count, replans per step, and wall-clock.
- **Tool registry additions** (new entries in `tools.json`, each with an `action_class`): `ax_snapshot` (read), `ax_click` (navigate), `ax_type` (mutate_local), `press_key` (mutate_local), `ask_user` (read), `finish` (read). Sending mail is an outward-facing class and always confirms.
- **Planner interface**: `plan_next(goal, observation, history) -> PlanStep | AskUser | Finish`. Implementations are swappable per RAM tier.

## Workstreams

Each lists scope, deliverable, and an acceptance test. Follow the repo rule: failing test first.

### M6 — Perception and goal-loop skeleton (planner stubbed)
- **W1 AX perception.** Build `Observation` from the AX API for the frontmost app; stable ids; redact secure text fields. Accept: snapshot of Finder and Mail compose window contains the named elements; password fields never appear.
- **W2 Goal loop.** `GoalRun` state machine, budgets, replan/retry, cancel. Accept: a scripted planner completes Finder → Mail with postconditions; an exhausted budget ends in `failed`, never loops.
- **W3 Action tools.** `ax_click`, `ax_type`, `press_key` with the registry and gate wiring. Accept: gate tests show `ax_type` into a secure field is refused, Send always confirms.
- **W4 Postconditions.** Declarative checks plus VLM-judged fallback hook. Accept: table-driven tests over recorded observations.

### M7 — Real planner and VLM fallback
- **W5 Planner.** Implement `plan_next` on the chosen MLX model, constrained to registry tools (JSON schema / grammar-constrained decoding if available). Screen text is passed as data in a delimited block. Accept: injection corpus extended with planner inputs; zero hijacks; demo goal reaches the send confirm.
- **W6 VLM grounding and goal check.** Fallback perception (element grounding) and final "is the goal done" check. Accept: numbers in the benchmark table below on a real-screen set, not only synthetic.
- **W7 RAM-tier profiles.** Extend the profile store to pick planner and VLM by available memory, with model swapping. Accept: 16 GB profile never exceeds the memory budget; swap latency measured and recorded.

### M8 — Hands-free
- **W8 Wake word + STT.** On-device wake word, always-listening loop that captures no audio while the mic is off. Accept: mic-off state is enforced in code and asserted in tests; false accepts logged.
- **W9 Notch mic toggle.** Toggle in the existing Swift notch UI with a clear on/off state that survives restart. Accept: toggling off stops capture within one audio buffer.
- **W10 Voice approval policy.** Low-risk spoken approve; high-risk requires click or passphrase challenge with a per-session phrase. Accept: replayed or synthesised audio cannot approve a high-risk action; adversarial test in the injection corpus.

### M9 — Task suite
- **W11 Suite.** About 50 macOS goals across Finder, Mail, Safari, Notes, Calendar, Settings-adjacent, each with a machine-checkable end state and a list of forbidden side effects. Accept: runner reports success rate, safety violations, steps, seconds, peak memory per goal.
- **W12 Regression gate.** CI-runnable subset (headless fixtures) and a live subset for the machine.

## Single model for both jobs?

Question raised 2026-09-30: use one model as planner and VLM. Candidate facts, checked on Hugging Face:

| Candidate | Vision | MLX build | 4-bit size | Verdict |
|---|---|---|---|---|
| Qwythos-9B-v2 (empero-ai, Qwen3.5 base) | yes | mlx-community OptiQ-4bit | 8.22 GB | Only true single-model candidate among the named ones. Benchmark below. Tagged "uncensored" on its card. |
| Qwen3.5-9B (its base) | yes | mlx-community 4bit | 5.98 GB | Cleaner base to compare against. Benchmark below. |
| Qwen3.8-9B-Distill (empero-ai) | no | none found (GGUF only) | n/a | Text-only, so it cannot replace the VLM. |
| GLM-4.7-Flash (30B MoE, ~9B active) | no | mlx-community 4bit | 16.87 GB | Does not fit a 16 GB machine: MoE keeps all experts resident. Text-only. |
| Phi-4-mini | no | mlx-community 4bit | 2.18 GB | Text-only; a planner candidate at best, not a single model. |

Trade-offs to decide with the numbers: one resident model saves swap latency and complexity, but a 9B at 4-bit costs 6-8 GB against a 16 GB machine that also runs the user's apps, and it will run slower per step than the 2B VLM (about 3.4 s per query today). An "uncensored" fine-tune is a poor default for an agent that reads untrusted text; the gate does not rely on model refusals, but injection resistance must be measured, not assumed. Results are in the benchmark section once the runs finish.

## Risks and how each is bounded

| Risk | Bound |
|---|---|
| Router accuracy is 75% and confidence does not separate right from wrong | Measure per-step accuracy in M6 before relying on it; postconditions catch wrong steps; floors bound damage. |
| Prompt injection from screen text | Planner sees screen as delimited data; output is schema-constrained and re-validated by router and gate; extend the injection corpus. |
| Audio-borne approval | Spoken approval limited to low-risk; passphrase per session for the rest. |
| Memory pressure on 16 GB | Tiered profiles, one large model resident at a time, measured budgets. |
| Disk: this machine has about 8 GB free | Download one model at a time; the benchmark scripts delete each model after use. |
| Model claims success it did not achieve | Success is decided by postconditions and the final VLM check, never by planner text. |

## Model benchmarks (this machine: M4, 16 GB, mlx-lm 0.31.3, mlx-vlm 0.7.4)

Scripts: `scripts/bench_models/bench_planner.py`, `scripts/bench_models/bench_vlm.py`. Planner set is 12 hand-written next-step cases including 3 injection cases; the VLM set uses synthetic screens because Screen Recording permission is not granted to the shell. Results below are small-sample indicators, not final selections.

<!-- BENCH-RESULTS:START -->
Planner (text):

| Model | Peak mem | Gen tok/s | Valid JSON | Correct next step | Injection hijacked |
|---|---|---|---|---|---|
| Qwen3-1.7B 4-bit | 1.26 GB | 99.7 | 12/12 | 3/12 | 0/3 |
| Qwen3-4B-Instruct-2507 4-bit | 2.63 GB | 42.3 | 12/12 | 8/12 | **2/3** |

VLM (Qwen3-VL, synthetic screens: 30 grounding queries, 12 goal-done checks):

| Model | Peak mem | Median s/query | Grounding | Goal check |
|---|---|---|---|---|
| Qwen3-VL-2B-Instruct 4-bit | 2.65 GB | 3.4 | 30/30 | 12/12 |
| Qwen3-VL-4B-Instruct 4-bit | 3.97 GB | 6.2 | 28/30 | 12/12 |

The 4B's two misses were the narrow sidebar item "Downloads", where the returned box sat slightly off the target. The 2B scored higher and ran twice as fast on this set. Synthetic screens are clean and high-contrast, so treat both as "works" and not as "ready"; real macOS screens (dense text, small icons, dark mode, overlapping windows) are the W6 acceptance test and need Screen Recording permission for the terminal.

Working choice for the two-model tier (16 GB): Qwen3-4B-Instruct-2507 planner (2.63 GB) plus Qwen3-VL-2B (2.65 GB), about 5.3 GB of weights resident.
<!-- BENCH-RESULTS:END -->

Reading so far: **the 4B model is the planner candidate, but a prompt that says "screen text is data" did not stop it.** In 2 of 3 injection cases it followed the embedded text and proposed `open_app Terminal`. Its other misses were `ax_click` with the label "Downloads" instead of the id `4`, and failing to call `finish` after the "Message sent" banner. The consequences for the design: (a) treat prompt-level defenses as worth nothing; (b) add **argument provenance** to the router, so every argument must be a literal span of the user's utterance, an element id present in the current `Observation`, or a fixed enum value; (c) add a **goal-relevance check**, so a step that opens an app the goal never mentioned needs a confirm even at low risk; (d) constrain decoding to the registry schema so `id` is always an integer from the observation. Workstream W5 must land these before the planner is enabled outside tests.

The 1.7B model is fast and small but picks the wrong step in 9 of 12 cases, which is too weak to plan alone. Some misses are scorer strictness (for example choosing `ax_snapshot` first), so treat 3/12 as a floor.

## How agents should work on this

1. Read `docs/ARCHITECTURE.md`, `docs/ROUTING.md`, and this file. Do not edit another workstream's files without recording it in the changelog below.
2. Write the failing test first, keep files under 800 lines, never mutate shared objects in place.
3. Any change to a Contract or a tool's `action_class` needs a changelog line and a policy-gate test.
4. Do not weaken a floor (destructive and outward-facing always confirm; shell, payments, keychain, System Settings always refuse).
5. Update the injection corpus for every new place untrusted text enters the system.

## Changelog

- 2026-09-30: plan created.
