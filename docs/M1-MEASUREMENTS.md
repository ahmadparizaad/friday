# M1 measurements — laya-mlx on this machine

M4 MacBook Air, 16GB, macOS 26.5.1, Python 3.12.13, `laya-mlx` 0.2.0,
checkpoint `aac6fef/laya-mlx` @ `20aed815fc6acde75733882e7ec0e3f28aeb9717`.
Measured 2026-09-23.

These numbers contradict two assumptions in ARCHITECTURE.md as originally
written. Both are corrected there; this file is the evidence.

---

## 1. The result shape is not what §5.2 assumed

Assumed (from the README's `print(result["answers"])` example):

```python
{"answers": {"tool": "open_app"}, "action": {"act_probability": 0.94}}
```

Actual:

```json
{
  "model": "laya-rl-agent",
  "answers": {
    "tool": {
      "type": "choice",
      "confidence": 0.8715,
      "action": {"act_probability": 1.0},
      "choice": "open_app",
      "probabilities": {"open_app": 0.9722, "focus_app": 0.0223,
                        "speak_time": 0.0056}
    },
    "followup": {"type": "noul", "confidence": 0.8282,
                 "action": {"act_probability": 1.0}, "noul": 0.1718}
  },
  "usage": {"input_tokens": 92, "output_tokens": 0}
}
```

- `answers.<name>` is a **dict**, not a bare label. The label is `.choice`.
- `act_probability` is **nested per answer**, not top level.
- `output_tokens: 0` — the no-generation property (§1) confirmed directly.

## 2. `act_probability` is unusable as a gate

**It reads 1.0 on every call measured**, across correct routes, wrong routes,
injections and gibberish. ARCHITECTURE.md §2 called it "the raw material for
the autonomy slider (R7)". **That was wrong.** It carries no routing signal.

Usable signals instead:
- `answers.tool.confidence` — calibrated scalar, but see §4 below.
- `max(answers.tool.probabilities.values())` — the winning option's share.

The router now uses top probability, and the policy gate is fed that.

## 3. Routing accuracy: 9/12 on a hand-written M1 set

Tool descriptions phrased as *what the user says*, which measured better than
phrasing them as *what the tool does*.

| utterance | expected | got | top_p |
|---|---|---|---|
| open safari | open_app | open_app | 0.9396 |
| launch terminal | open_app | open_app | 0.9033 |
| open notes | open_app | open_app | 0.6696 |
| start music | open_app | open_app | 0.7647 |
| switch to notes | focus_app | focus_app | 0.4589 |
| **bring mail to the front** | focus_app | **open_app** | 0.5150 |
| focus safari | focus_app | focus_app | 0.7328 |
| **go to finder** | focus_app | **open_app** | 0.7457 |
| **what time is it** | speak_time | **open_app** | 0.4564 |
| tell me the time | speak_time | speak_time | 0.7255 |
| what is the current time | speak_time | speak_time | 0.9137 |
| say the time | speak_time | speak_time | 0.7393 |

**75% accuracy.** `open_app` is the failure sink — every error lands there,
suggesting it absorbs probability mass as the "generic" option.

## 4. Confidence does not separate correct from incorrect

| | min | median | max |
|---|---|---|---|
| correct routes | 0.459 | 0.739 | 0.940 |
| incorrect routes | — | 0.515 | **0.746** |

**The ranges overlap almost entirely.** Any threshold low enough to admit the
correct 0.459 route also admits every wrong route. Any threshold above 0.746
rejects half the correct ones.

This is the single most important M1 finding and it invalidates open question
#3 as posed ("slider defaults stay conservative until logs show the real
spread"). The spread is now known: **there is no clean separation to tune.**

A `RuntimeWarning` also fires at load — the checkpoint ships a `choice:11+`
temperature of 0.1006, clamped to 0.5, with laya's own advice to
"treat confidence from the affected buckets as uncalibrated". M1 uses 3
options so it is not in that bucket, but a registry past 10 tools would be.

## 4b. Tried and rejected: a "none of these" escape option

Hypothesis: adding a fourth option `none` ("none of these; the request is
something else") would drain the `open_app` sink and give the model a way to
express "I don't know".

| | real-utterance accuracy | junk inputs routed to `none` |
|---|---:|---:|
| baseline (3 options) | **9/12** | 0/3 |
| with `none` (4 options) | **3/12** | **3/3** |

It works *too well*: `none` perfectly captured all three junk inputs
(gibberish, an injection, an out-of-scope request) but became the new sink,
swallowing two thirds of the legitimate ones. **Net loss — not adopted.**

The useful part of the result: the model *can* express "not this" when given
the option, so the idea is sound and its *form* is what failed.

## 4c. Promising lead: an out-of-scope `noul` gate

Retried as a **separate `noul` question** alongside the choice, so it competes
for no probability mass. Confirmed: **routing accuracy stayed at 9/12** while
the noul independently scored whether the utterance was in scope at all.

Three phrasings, over 12 real utterances and 6 junk/hostile ones:

| phrasing | real (min / median) | junk (max / median) | |
|---|---|---|---|
| A: "Is the user asking to open an app, switch to an app, or ask the time?" | 0.105 / 0.481 | **0.109** / 0.083 | overlap of 0.004 |
| B: "Does this request match one of the available actions?" | 0.002 / 0.443 | 0.953 / 0.726 | inverted, useless |
| C: "Is this a command to control an application or ask the time?" | 0.126 / 0.716 | 0.953 / 0.461 | wide overlap |

**Phrasing A nearly separates** — real utterances bottom out at 0.105, junk
tops out at 0.109. A 0.004 gap on an 18-sample set is *not* a gate anyone
should ship, but it is a much better signal than the choice confidence
(§4), which overlaps by ~0.29.

Note phrasing matters enormously and not intuitively: A enumerates the
concrete actions and works; B refers abstractly to "available actions" and
inverts completely. The model is not reading the option list when answering
the noul — it only sees the words in the instruction.

**Next step (M2):** re-test phrasing A on a corpus of 100+ utterances. If the
separation holds, an out-of-scope noul becomes a cheap pre-filter that refuses
before the choice is even consulted — which would attack the 75% accuracy
problem from the other side, by removing the inputs that have no right answer.

## 5. What this means for the design

The floors in §5.4 already absorb this, which is the point of having them:

- `mutate_local` / `mutate_remote` / `destructive` never auto-allow **at any
  confidence**, so a 0.746-confidence wrong route into a dangerous class still
  stops at a confirmation.
- `blocked` refuses unconditionally.
- The damage from a wrong route is therefore bounded by the *class* of the
  tool it wrongly picked, never by how sure the model was.

What must change:
1. **Raise the ALLOW threshold** so auto-execution requires near-certainty
   (0.90 CAUTIOUS is retained; the measured correct-route median of 0.739 means
   most correct routes will confirm rather than auto-run — the right trade at
   75% accuracy).
2. **Do not ship PERMISSIVE as a recommended setting** until accuracy improves.
   At 0.65 it would auto-run wrong routes.
3. Open question #2 (Choice width) is now urgent rather than deferred: with 3
   tools the model already confuses two of them.

## 6. Latency

Measured by `pytest -m live` (n=20, warm, model load excluded), routing one
utterance over a 2-question batch (choice + noul):

| | this M4 Air | published M3 Max |
|---|---:|---:|
| p50 | **83.3 ms** | 13.4 ms |
| p95 | **114.3 ms** | 13.9 ms |

**~6x the published figure**, which is why §2 of ARCHITECTURE.md said to measure
rather than inherit the number. Still comfortably interactive: the round trip
is dominated by STT and the user's own speech, not by routing.

This answers open question #6: at ~83ms a routing spinner would be visible for
~5 frames — brief, but not a single-frame flash. A spinner is defensible; it
should appear only after a short delay so the common case stays silent.

Model load (cold, weights already on disk) is several seconds, which is why the
`Agent` is constructed once and kept alive (§7).
