# Browser automation skill plan

Status: plan only, written 2026-10-03. Companion to `docs/PLAN-SKILLS.md` (skills layer) and `docs/ARCHITECTURE.md` §5.6 (browser control table). Nothing here is built yet.

## Goal

Let a skill drive a real browser for multi-step web tasks ("open GitHub, search for X, open the first result", "fill the shipping form with my saved address", "download the latest invoice") **without** giving any component the ability to emit free text, free URLs, or free selectors. Same bar as the rest of Friday: the model proposes from a fixed set, deterministic code disposes.

## What exists today

| Piece | State |
|---|---|
| `browser.py` CDP backend | Reads the accessibility tree of **one** tab, builds a numbered control table, and can read / click / submit a control by index. `search()` navigates the current tab to a templated URL. |
| `browser_*` tools | `browser_snapshot`, `browser_read`, `browser_click`, `browser_submit`, `browser_search`. One action per utterance; the user must say "click 3". |
| `open_url` | Closed shortcut table (`shortcuts.py`): email, youtube, github, wikipedia. |
| Skill backends | `applescript`, `cli`, `shortcuts`, `url`. **No browser backend**, so a skill cannot sequence browser steps today. |
| Debug port | CDP needs `--remote-debugging-port=9222`. Nothing launches a browser that way, so snapshot/click/read currently fail with "browser not reachable". `search` now falls back to `open <url>` for exactly this reason. |

## Decisions needed before building (not made yet)

1. **Which browser profile does automation touch?**

   | Option | Pros | Cons |
   |---|---|---|
   | A. Dedicated Friday profile (`--user-data-dir=~/Library/Application Support/friday/browser-profile`, debug port on 127.0.0.1) | Daily logins and cookies are out of reach of any bug or injected page. Works with current Chrome, which ignores the debug port on the default profile. | The user logs in once per site inside that profile. Two browsers to keep in mind. |
   | B. Real profile via AppleScript `execute javascript` | Uses existing logins. | Needs "Allow JavaScript from Apple Events", gives page-level code execution in the logged-in profile, no accessibility-tree table. Much larger blast radius. |
   | C. Browser extension | Best fidelity. | New distribution and trust surface; out of scope for now. |

   **Recommendation: A.** I have not verified on this machine that the installed Chrome and Brave refuse the debug port on the default profile, and that is the first thing to check (Phase 0).

2. **Which sites may a skill touch?** Recommendation: each skill declares `allowed_hosts` in its manifest, and navigation outside that list is refused. No spoken or page-supplied URL is ever navigated to.

3. **Credentials.** Recommendation: never typed by Friday. Password and OTP fields are blocked; the user logs in manually in the Friday profile.

## Design

### A `browser` skill backend

A new backend alongside `applescript` / `cli` / `url`, whose steps are a **closed verb set**, not scripts:

| Verb | Arguments | Action class |
|---|---|---|
| `goto` | `site` from a reviewed table or the skill's `allowed_hosts`, plus a path template with percent-encoded slots | `navigate` |
| `snapshot` | none (refreshes the control table) | `read` |
| `read` | control selector | `read` |
| `click` | control selector | `navigate` (link) / `mutate_remote` (button inside a form) |
| `type` | control selector + slot of type `text` | `mutate_local` until submitted |
| `select` | control selector + enum value | `mutate_local` |
| `scroll` | `up` / `down` / `top` / `bottom` | `read` |
| `wait_for` | role+name pattern, bounded timeout | `read` |
| `tab` | `new` / `close` / `next` / `previous` | `navigate` |
| `download` | none; lands in a quarantined directory | `mutate_local` |

There is no `eval`, no raw JS, no `fetch`, and no free selector. A control is chosen by a **selector** (role + accessible-name pattern declared in the manifest), resolved against a fresh table at execution time. The index-only rule from `browser.py` still holds: a stale or ambiguous match refuses and does not guess.

### Manifest sketch

```json
{
  "id": "web.github_repo_search",
  "triggers": ["search github for {query} and open the first result"],
  "slots": {"query": {"type": "text", "provenance": "utterance", "max_len": 120}},
  "action_class": "navigate",
  "allowed_hosts": ["github.com"],
  "requires": {"browser": "friday-profile"},
  "steps": [
    {"id": "go",   "backend": "browser", "verb": "goto", "site": "github.com", "path": "/search?q={query}"},
    {"id": "wait", "backend": "browser", "verb": "wait_for", "selector": {"role": "link", "name": "^.+/.+$"}},
    {"id": "open", "backend": "browser", "verb": "click", "selector": {"role": "link", "nth": 0, "within": "search-results"}}
  ],
  "postcondition": {"type": "url_host_is", "value": "github.com"}
}
```

### Safety rules

- **Page content is data.** Page text, titles and labels never become a slot, a URL, or a selector. A hostile page can at worst make a selector fail.
- **Closed navigation.** `goto` resolves against `allowed_hosts` / the shortcut table. Redirects off an allowed host stop the skill (checked after every navigation and click).
- **Outward-facing steps confirm.** Any `click` that submits a form, and any `type` followed by a submit, is `mutate_remote`, so it always confirms. The confirmation reads back the exact target control and typed text verbatim.
- **Blocked controls.** Password, OTP, payment-card, and fields whose accessible name matches a deny list are refused. Pages on payment or banking hosts are refused.
- **Bounded runs.** Per-skill step cap, wall-clock timeout, and a single tab. Failure at any step stops the run and records the failure in the audit log with the step id and the sanitized selector.
- **Downloads** go to a dedicated directory and are never opened or executed automatically.
- **Audit.** Every step is logged with verb, resolved control (role + name), host before and after.

## Phases (test first in each)

| Phase | Work | Verify |
|---|---|---|
| 0. Spike | Launch Chrome and Brave with `--remote-debugging-port` on default vs dedicated profile and record which accept it. Add `friday browser launch` that starts the Friday profile. | A recorded matrix in `docs/` and a passing `test_browser_live` against the Friday profile. |
| 1. Backend | `skills/backends/browser.py` with `goto`, `snapshot`, `read`, `click`, `wait_for`; selector resolver; host allow-list. | Unit tests on `FakeBackend` (canned pages), including stale-index, ambiguous-selector, off-host redirect refusal. |
| 2. Manifest and validator | Add `verb`, `selector`, `allowed_hosts`, and `requires.browser` to the manifest schema and validator; reject unknown verbs, scripts, and free URLs. | Validator tests for each rejection; `friday skill validate --json` golden output. |
| 3. First skills | `web.github_repo_search`, `web.open_site_and_search` (read-only), then one form skill that stops at the submit confirm. | Dry-run golden tests plus a live run in the Friday profile. |
| 4. Input verbs | `type`, `select`, `scroll`, `tab`, `download` with the blocked-control list. | Tests that password/OTP/payment fields refuse, and that `type` without a following confirmed submit never reaches `mutate_remote`. |
| 5. Router and voice | Teach the router a `web_skills` category (or reuse `skills`) and add utterances to `tests/fixtures/routing_corpus.json`; measure with `scripts/eval_routing.py`. | No regression on the held-out split. |

## Open questions

- Should the Friday profile's browser be Chrome or Brave? (`open_url` shortcuts currently point at Chrome, except email → Brave.)
- Do we want the user's own window to stay frontmost, or should automation run in a background window?
- How should the user be told a run is in progress and be able to stop it by voice (a "stop" that cancels the skill between steps)?
