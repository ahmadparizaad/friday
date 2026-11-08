"""Execute registry tools — ARCHITECTURE.md §5.6 (R3).

Two properties this module exists to guarantee:

1. The model never emits AppleScript. Scripts are pre-written files in
   `scripts/`; we pass arguments via argv, never by string interpolation,
   so transcript text can never become script text. A tool may instead
   declare `binary` (a small, separate allow-list of trusted system
   binaries, e.g. `screencapture`) — same argv-only rule applies.
2. Arguments are re-validated against the registry here, even though
   `slots` already validated them. Defence in depth: this is the last
   gate before something actually happens.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from friday.registry import REGISTRY

SCRIPT_DIR = Path(__file__).parent / "scripts"

TIMEOUT_SECONDS = 15

SCREENSHOT_DIR = Path.home() / "Desktop"


class ExecutionError(Exception):
    """The action was not run."""


@dataclass(frozen=True)
class Result:
    ok: bool
    output: str


def _validate(tool_name: str, arguments: dict[str, str]):
    tool = REGISTRY.get(tool_name)
    if tool is None:
        raise ExecutionError(f"unknown tool {tool_name!r}")

    expected = set(tool.arguments)
    given = set(arguments)
    if given != expected:
        raise ExecutionError(
            f"{tool_name} expects arguments {sorted(expected)}, got {sorted(given)}"
        )

    if any(spec["type"] == "url_shortcut" for spec in tool.arguments.values()):
        # Friday's table maps a word to a host, and the browser is
        # a separate closed table (shortcuts.BROWSERS). Re-check both here
        # rather than trusting whatever slots produced (defence in depth).
        from friday.shortcuts import BROWSERS, DEFAULT_BROWSER, _table

        url, browser = arguments["url"], arguments["browser"]
        if url not in set(_table().values()):
            raise ExecutionError(f"{tool_name} url {url!r} is not in the shortcut table")
        if browser not in set(BROWSERS.values()) | {DEFAULT_BROWSER}:
            raise ExecutionError(f"{tool_name} browser {browser!r} is not allow-listed")
        return tool

    for arg_name, spec in tool.arguments.items():
        value = arguments[arg_name]
        kind = spec["type"]

        if kind == "enum":
            allowed = spec["values"]
        elif kind == "installed_app":
            # Re-check against the scan, not just against whatever slots
            # produced. This is the last gate before osascript runs, and
            # it must not become a pass-through: when the app enum became
            # a dynamic scan, an unvalidated `installed_app` let injection
            # payloads reach osascript (they hit the 15s timeout instead
            # of being refused). Defence in depth means *this* layer
            # validates too, independently.
            from friday.registry import known_apps

            allowed = known_apps()
        elif kind in _NEW_KINDS:
            _validate_new_kind(tool_name, arg_name, kind, value, arguments)
            continue
        else:
            raise ExecutionError(
                f"{tool_name}.{arg_name} has unvalidatable type {kind!r}"
            )

        if value not in allowed:
            raise ExecutionError(
                f"{tool_name}.{arg_name}={value!r} is not in its allow-list"
            )
    return tool


# --- validation for the 16-tool set ----------------------------------------------

_NEW_KINDS = frozenset({"literal_span", "control_index", "skill_ref", "skill_slots"})
MAX_SPAN = 500


def _validate_new_kind(tool_name, arg_name, kind, value, arguments) -> None:
    where = f"{tool_name}.{arg_name}"
    if kind == "literal_span":
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_SPAN:
            raise ExecutionError(f"{where} must be a non-empty span of at most {MAX_SPAN} chars")
    elif kind == "control_index":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ExecutionError(f"{where} must be a non-negative integer")
    elif kind == "skill_ref":
        from friday.skills.store import get_enabled

        if not isinstance(value, str) or get_enabled(value) is None:
            raise ExecutionError(f"{where}={value!r} is not an enabled skill")
    elif kind == "skill_slots":
        if not isinstance(value, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        ):
            raise ExecutionError(f"{where} must map slot names to strings")


def _browser_tool(tool_name: str, arguments: dict) -> Result:
    from friday import browser

    try:
        if tool_name == "browser_snapshot":
            out = browser.snapshot()
        elif tool_name == "browser_read":
            out = browser.read_control(arguments["control"])
        elif tool_name == "browser_click":
            out = browser.click_control(arguments["control"])
        elif tool_name == "browser_submit":
            out = browser.submit_control(arguments["control"])
        elif tool_name == "browser_search":
            site, query = arguments["site"], arguments["query"]
            if site == "current":
                out = browser.search_here(query)
            elif site == "current_new_tab":
                out = browser.search_here(query, new_tab=True)
            else:
                out = browser.search(site, query)
        elif tool_name == "navigate_tab":
            out = browser.open_new_tab(arguments["browser"])
        elif tool_name == "close_tab":
            out = browser.close_current_tab()
        else:
            raise ExecutionError(f"no implementation for {tool_name!r}")
    except browser.BrowserError as exc:
        raise ExecutionError(str(exc)) from exc
    return Result(ok=True, output=out)


def _run_skill(arguments: dict) -> Result:
    from friday.skills.runtime import run_skill
    from friday.skills.store import get_enabled

    skill = get_enabled(arguments["skill_id"])
    if skill is None:  # re-checked: the pin may have changed since validation
        raise ExecutionError(f"skill {arguments['skill_id']!r} is no longer enabled")
    result = run_skill(skill, arguments["slots"])
    if not result.ok:
        raise ExecutionError(result.output or f"skill {skill.id} failed")
    return Result(ok=True, output=result.output)


_BROWSER_TOOLS = frozenset(
    {"browser_snapshot", "browser_read", "browser_click", "browser_submit",
     "browser_search", "navigate_tab", "close_tab"}
)



def execute(tool_name: str, arguments: dict[str, str], ctx=None) -> Result:
    """Run a registry tool. Raises ExecutionError rather than running
    anything it cannot fully validate.

    `ctx` is goal.py's GoalContext. No registered tool needs it
    yet; it is accepted so the goal runner's call shape is valid.
    """
    tool = _validate(tool_name, arguments)

    if tool.binary is not None:
        return _run_binary(tool_name, tool.binary)

    if tool.script is None:
        if tool_name in _BROWSER_TOOLS:
            return _browser_tool(tool_name, arguments)
        if tool_name == "run_skill":
            return _run_skill(arguments)
        return _builtin(tool_name)

    script_path = SCRIPT_DIR / tool.script
    if not script_path.exists():
        raise ExecutionError(f"script {tool.script} missing from allow-list")

    # Arguments go through argv, bound by name to the registry's declared
    # order rather than the caller's dict order — code-reviewer caught
    # that `*arguments.values()` would silently swap positions for a
    # differently-ordered but still-valid dict (e.g. {"browser": ...,
    # "url": ...}), sending each value to the wrong script parameter
    # despite passing validation. There is no shell, and no string
    # interpolation into the script body.
    argv = ["osascript", str(script_path)] + [
        arguments[name] for name in tool.arguments
    ]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(f"{tool_name} timed out") from exc

    if proc.returncode != 0:
        raise ExecutionError(proc.stderr.strip() or f"{tool_name} failed")
    return Result(ok=True, output=proc.stdout.strip())


def _builtin(tool_name: str) -> Result:
    """Tools implemented in-process, with no script and no side effects."""
    if tool_name == "speak_time":
        return Result(ok=True, output=datetime.now().strftime("It is %-I:%M %p"))
    raise ExecutionError(f"no implementation for {tool_name!r}")


def _run_binary(tool_name: str, binary: str) -> Result:
    """Run a registry tool backed by a trusted system binary rather than
    an AppleScript file — a separate, equally narrow allow-list (§5.6).
    Only `screencapture` is registered today; a new entry here is as
    deliberate an act as adding a new .applescript file.

    Re-checked against the same allow-list registry.py's loader already
    checked at import time — defence in depth, same reasoning as
    installed_app/url_shortcut above: this is the last gate before
    anything actually runs.
    """
    from friday.registry import ALLOWED_BINARIES

    if binary not in ALLOWED_BINARIES:
        raise ExecutionError(f"{binary!r} is not an allow-listed binary")

    resolved = shutil.which(binary)
    if resolved is None:
        raise ExecutionError(f"{binary} is not available on this machine")

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filename = datetime.now().strftime("Screenshot %Y-%m-%d at %H.%M.%S.png")
    target = SCREENSHOT_DIR / filename

    # No shell, no argv built from anything but a fixed path we just
    # constructed ourselves — nothing here is transcript text.
    try:
        proc = subprocess.run(
            [resolved, "-x", str(target)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(f"{tool_name} timed out") from exc

    if proc.returncode != 0:
        raise ExecutionError(proc.stderr.strip() or f"{tool_name} failed")
    return Result(ok=True, output=str(target))
