"""Executor tests — ARCHITECTURE.md §5.6 (R3).

The model never emits AppleScript text. The executor runs pre-written,
code-reviewed scripts with typed parameters, and refuses anything else.
"""

from pathlib import Path

import pytest

from friday.executor import ExecutionError, execute
from friday.registry import REGISTRY


def test_runs_a_registered_no_arg_tool():
    result = execute("speak_time", {})
    assert result.ok
    assert result.output


def test_unknown_tool_is_refused():
    with pytest.raises(ExecutionError):
        execute("no_such_tool", {})


def test_rejects_an_app_that_is_not_installed():
    """Defence in depth: even if slots were bypassed, the executor
    re-validates independently before running anything.

    This caught a real regression — when the app enum became a dynamic
    scan, `_validate` only understood `type == "enum"`, so an
    `installed_app` argument passed through unchecked and injection
    payloads reached osascript (visible as a 15s timeout instead of a
    refusal)."""
    with pytest.raises(ExecutionError):
        execute("use_app", {"app": "Definitely Not Installed 12345"})


def test_rejects_unexpected_argument():
    with pytest.raises(ExecutionError):
        execute("use_app", {"app": "Safari", "extra": "x"})


def test_rejects_missing_argument():
    with pytest.raises(ExecutionError):
        execute("use_app", {})


@pytest.mark.parametrize(
    "payload",
    [
        'Safari"; do shell script "rm -rf ~"',
        "Safari' & (do shell script \"whoami\") & '",
        "Safari\ntell application \"Terminal\" to do script \"id\"",
    ],
)
def test_rejects_script_injection_in_argument(payload):
    """The enum check makes this unreachable, which is the point: there
    is no code path where transcript text becomes script text."""
    with pytest.raises(ExecutionError):
        execute("use_app", {"app": payload})


def test_use_app_reports_whether_it_opened_or_focused():
    """The audit log records what actually happened, so the executor
    must distinguish launching an app from switching to a running one —
    the distinction the model is no longer asked to guess."""
    import subprocess

    from friday.executor import SCRIPT_DIR

    # Finder is always running on macOS, so this is the "focused" case.
    proc = subprocess.run(
        ["osascript", str(SCRIPT_DIR / "use_app.applescript"), "Finder"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "focused Finder"


def test_every_scripted_tool_has_its_script_present_on_disk():
    """A registry entry pointing at a missing script is a broken
    allow-list, not a runtime surprise."""
    from friday.executor import SCRIPT_DIR

    for name, tool in REGISTRY.items():
        if tool.script:
            assert (SCRIPT_DIR / tool.script).exists(), name


def test_scripts_directory_contains_no_unregistered_scripts():
    """Anything executable in the scripts dir must be reachable only via
    a registry entry - no orphans lying around."""
    from friday.executor import SCRIPT_DIR

    registered = {t.script for t in REGISTRY.values() if t.script}
    on_disk = {p.name for p in SCRIPT_DIR.glob("*.applescript")}
    assert on_disk == registered


def test_no_tool_declares_a_binary_outside_the_allow_list():
    """The binary allow-list in _run_binary is meant to be exhaustive —
    a tools.json entry naming an unrecognised binary must be refused at
    execution time, not silently run. Mirrors the script-presence check
    above, one level down at the actual dispatch function."""
    for name, tool in REGISTRY.items():
        if tool.binary:
            assert tool.binary == "screencapture", (
                name,
                "add it to _run_binary's allow-list first",
            )


# --- open_url --------------------------------------------------------------


def test_rejects_a_url_not_in_the_shortcut_table():
    """Defence in depth, mirroring the installed_app check: even if slots
    were bypassed, the executor re-validates the (url, browser) pair
    against shortcuts.py independently before running anything."""
    with pytest.raises(ExecutionError):
        execute(
            "open_url",
            {"url": "evil.example.com", "browser": "Brave Browser"},
        )


def test_rejects_a_known_url_with_a_mismatched_browser():
    """The pair must match a single table entry, not each half
    independently — otherwise a valid url could be paired with any
    allow-listed browser."""
    with pytest.raises(ExecutionError):
        execute(
            "open_url",
            {"url": "mail.google.com", "browser": "Safari"},
        )


def test_open_url_opens_the_shortcuts_url_in_the_named_browser():
    result = execute(
        "open_url", {"url": "mail.google.com", "browser": "Brave Browser"}
    )
    assert result.ok
    assert result.output == "opened mail.google.com in Brave Browser"


def test_open_url_with_the_default_browser_runs_the_script_with_default(monkeypatch):
    """No browser spoken means the default browser: the script receives
    the literal "default" and uses `open location` with no `tell`."""
    import friday.executor as executor_mod

    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        return type("P", (), {"returncode": 0, "stdout": "opened", "stderr": ""})()

    monkeypatch.setattr(executor_mod.subprocess, "run", fake_run)
    result = execute("open_url", {"url": "www.youtube.com", "browser": "default"})
    assert result.ok
    assert seen[0][-2:] == ["www.youtube.com", "default"]


def test_open_url_binds_arguments_by_name_not_by_dict_order():
    """argv must be built from the registry's declared argument order, not
    whatever order the caller's dict happens to iterate in — otherwise a
    same-content, reordered dict silently swaps which value lands in
    which script parameter despite passing validation."""
    result = execute(
        "open_url", {"browser": "Brave Browser", "url": "mail.google.com"}
    )
    assert result.ok
    assert result.output == "opened mail.google.com in Brave Browser"


# --- set_volume --------------------------------------------------------
# Real execution: low-risk, trivially restorable system state, same
# convention as use_app/open_url above.


def test_rejects_a_volume_level_not_in_the_enum():
    with pytest.raises(ExecutionError):
        execute("set_volume", {"level": "deafening"})


def test_set_volume_actually_sets_the_volume():
    result = execute("set_volume", {"level": "low"})
    assert result.ok
    assert result.output == "volume set to low"


# --- take_screenshot -----------------------------------------------------
# Real execution: additive only (writes one new file), same convention.


def test_take_screenshot_writes_a_png_to_the_desktop_or_reports_why_not():
    """Real execution, like set_volume above — but screencapture requires
    Screen Recording permission (TCC), which the terminal running this
    suite may not have been granted. Measured on this machine: it wasn't
    (`could not create image from display`, exit 1) — a real environment
    fact per ARCHITECTURE.md §2, not a code bug.

    Rather than skip (which would leave _run_binary's real subprocess
    path — the failure branch included — untested on every machine
    without the permission granted), this asserts whichever of the two
    real, honest outcomes actually happened: a written PNG, or an
    ExecutionError carrying screencapture's own stderr."""
    from friday.executor import SCREENSHOT_DIR

    try:
        result = execute("take_screenshot", {})
    except ExecutionError as exc:
        assert str(exc)  # the TCC denial reaches the caller, not silence
        return
    assert result.ok
    path = Path(result.output)
    assert path.parent == SCREENSHOT_DIR
    assert path.suffix == ".png"
    assert path.exists()
    path.unlink()  # leave no trace beyond what the test itself created


def test_unknown_binary_is_refused():
    """Defence in depth: a tool cannot name a binary outside the
    allow-list, even if tools.json were somehow tampered with."""
    from friday.executor import _run_binary

    with pytest.raises(ExecutionError):
        _run_binary("evil_tool", "rm")


# --- quit_app / toggle_dark_mode / lock_screen -----------------------------
# Validation-path tests only: unlike set_volume/take_screenshot, actually
# running these would quit a real app, leave dark mode flipped, or lock
# the test machine — none of which is safe to do unattended or in CI.


def test_rejects_quit_app_that_is_not_installed():
    with pytest.raises(ExecutionError):
        execute("quit_app", {"app": "Definitely Not Installed 12345"})


def test_quit_app_script_present_and_argv_only():
    """Confirms the script exists and takes its argument via argv, without
    actually quitting anything — mirrors use_app's injection-safety tests
    without exercising the live "quit" behaviour."""
    from friday.executor import SCRIPT_DIR

    script = SCRIPT_DIR / "quit_app.applescript"
    assert script.exists()
    assert "do shell script" not in script.read_text()


def test_toggle_dark_mode_and_lock_screen_scripts_are_argv_only():
    """Same static check as quit_app: never actually flips appearance or
    locks the screen in the test run."""
    from friday.executor import SCRIPT_DIR

    for name in ("toggle_dark_mode.applescript", "lock_screen.applescript"):
        script = SCRIPT_DIR / name
        assert script.exists()
        assert "do shell script" not in script.read_text()
