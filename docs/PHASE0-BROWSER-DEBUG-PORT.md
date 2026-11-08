# Phase 0: Browser Debug Port Spike

**Date**: 2026-10-04  
**Tested on**: macOS with Chrome 154.x, Brave 154.x, launched from a sandboxed terminal

## Test Matrix

| Browser | Profile | `--remote-debugging-port` | Result |
|---------|---------|---------------------------|--------|
| Chrome | Throwaway (`/tmp/chrome-test-profile`) | 9223 | ✅ Port honored |
| Chrome | Default (`~/Library/Application Support/Google/Chrome`) | 9224 | ⚠️ **Inconclusive** — browser never started |
| Brave | Throwaway (`/tmp/brave-test-profile`) | 9225 | ✅ Port honored |
| Brave | Default (`~/Library/Application Support/BraveSoftware/Brave-Browser`) | 9226 | ⚠️ **Inconclusive** — browser never started |
| Either | Friday's profile (`~/Library/Application Support/friday/browser-profile`), port 9222 | — | ❌ **Not tested** (directory does not exist yet; `friday browser launch` has not been run) |

## What was and was not shown

- The default-profile runs failed with "Operation not permitted" on `SingletonLock`.
  That is the *sandboxed terminal's* file-access limit. The browser process never
  started, so these runs say nothing about whether Chrome/Brave honor
  `--remote-debugging-port` on the default profile.
- An earlier draft attributed the failure to a macOS TCC restriction. That is
  **unproven**. The Friday app launching the browser would run with different
  permissions than this terminal, so the result would not transfer either way.
- Throwaway `/tmp` profiles on ports 9223–9226 show only that a non-default
  `--user-data-dir` exposes a working CDP endpoint
  (`/json/list`, websocket debugger URL). They are not Friday's profile or port.

## Recommendation (unchanged, reasons corrected)

Use the dedicated Friday profile, as in `docs/PLAN-BROWSER-SKILL.md`:

1. **Chrome ignores the debug port on the default profile.** Recent Chrome
   versions are documented to refuse `--remote-debugging-port` when the default
   user data directory is used; a non-default `--user-data-dir` is required.
   (Documented behavior, not verified by this spike. Brave is Chromium-based and
   expected to follow, also unverified.)
2. **Isolation.** A separate profile keeps the user's daily logins, cookies and
   extensions out of the automation's reach. The user logs in once per site
   inside the Friday profile.

## Still to verify

- Run `friday browser launch` for real (creates the Friday profile, binds 9222)
  and confirm `/json/list` answers.
- Run one live read-only skill through that browser.
- Default-profile behavior stays **untested**; do not cite it as evidence.
