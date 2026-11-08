"""Backend tests — docs/PLAN-SKILLS.md S2.

S2's acceptance: "no shell metacharacter in a slot value can change the
command; paths outside the allowed directories are refused". subprocess
is faked, so these assert the exact argv each backend would execute.
"""

import subprocess

import pytest

from friday.skills.backends import (
    AppleScriptBackend,
    BackendError,
    CliBackend,
    ShortcutsBackend,
    UrlBackend,
    fill_argv,
)
from friday.skills.backends import base
from friday.skills.manifest import Step

HOSTILE = ["; rm -rf ~", "$(id)", "`id`", "a | b", "x && y", "-rf", "\n", "{name}"]


@pytest.fixture
def ran(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="out\n", stderr="")

    monkeypatch.setattr(base.subprocess, "run", fake_run)
    return calls


def step(backend, argv, script=None):
    return Step(id="s", backend=backend, argv=tuple(argv), dry_run="x", script=script)


# --- fill_argv: a slot value is always exactly one argument ----------------


@pytest.mark.parametrize("value", HOSTILE)
def test_hostile_slot_value_is_one_verbatim_argument(value):
    assert fill_argv(("mdfind", "-name", "{name}"), {"name": value}) == [
        "mdfind",
        "-name",
        value,
    ]


def test_embedded_placeholder_is_refused():
    with pytest.raises(BackendError, match="whole argument"):
        fill_argv(("--name={name}",), {"name": "x"})


def test_missing_slot_is_refused():
    with pytest.raises(BackendError, match="no value"):
        fill_argv(("{name}",), {})


@pytest.mark.parametrize("value", HOSTILE)
def test_cli_runs_without_a_shell(ran, value):
    CliBackend().run(step("cli", ["mdfind", "{name}"]), {"name": value})
    argv, kwargs = ran[-1]
    assert argv == ["mdfind", value]
    assert kwargs["shell"] is False


# --- per-backend narrowing --------------------------------------------------


@pytest.mark.parametrize("binary", ["rm", "osascript", "bash", "sh", "curl"])
def test_cli_refuses_binaries_off_the_allow_list(ran, binary):
    with pytest.raises(BackendError, match="allow-listed"):
        CliBackend().run(step("cli", [binary, "x"]), {})
    assert ran == []


def test_cli_nonzero_exit_is_a_failed_result(monkeypatch):
    monkeypatch.setattr(
        base.subprocess,
        "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom"),
    )
    result = CliBackend().run(step("cli", ["mdfind", "x"]), {})
    assert not result.ok and result.output == "boom"


def test_timeout_raises_a_backend_error(monkeypatch):
    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(base.subprocess, "run", slow)
    with pytest.raises(BackendError, match="timed out"):
        CliBackend().run(step("cli", ["mdfind", "x"]), {}, timeout=0.1)


@pytest.mark.parametrize(
    "url",
    ["https://github.com/x", "http://example.com", "mailto:someone@example.com"],
)
def test_url_opens_allowed_schemes(ran, url):
    result = UrlBackend().run(step("url", ["{url}"]), {"url": url})
    assert result.ok and result.output == url
    assert ran[-1][0] == ["open", url]


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "file:///etc/passwd", "ssh://host", "https://", "x-apple.systempreferences:"],
)
def test_url_refuses_other_schemes_and_hostless_urls(ran, url):
    with pytest.raises(BackendError):
        UrlBackend().run(step("url", ["{url}"]), {"url": url})
    assert ran == []


def test_shortcuts_runs_one_named_shortcut(ran):
    ShortcutsBackend().run(step("shortcuts", ["{name}"]), {"name": "Focus; rm -rf"})
    assert ran[-1][0] == ["shortcuts", "run", "Focus; rm -rf"]


def test_applescript_runs_the_skill_script_with_slot_args(ran, tmp_path):
    (tmp_path / "x.applescript").write_text("on run argv\nend run")
    AppleScriptBackend(tmp_path).run(
        step("applescript", ["{name}"], script="x.applescript"), {"name": "$(id)"}
    )
    assert ran[-1][0] == ["osascript", str((tmp_path / "x.applescript").resolve()), "$(id)"]


@pytest.mark.parametrize(
    "script",
    ["../evil.applescript", "/etc/evil.applescript", "run.sh", "tests/x.applescript", None],
)
def test_applescript_refuses_scripts_outside_the_rules(ran, tmp_path, script):
    with pytest.raises(BackendError):
        AppleScriptBackend(tmp_path).run(step("applescript", ["a"], script=script), {})
    assert ran == []


def test_applescript_refuses_a_symlink_escaping_the_skill_folder(ran, tmp_path):
    outside = tmp_path / "outside.applescript"
    outside.write_text("x")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "link.applescript").symlink_to(outside)
    with pytest.raises(BackendError, match="not found"):
        AppleScriptBackend(skill).run(step("applescript", [], script="link.applescript"), {})
    assert ran == []


def test_applescript_without_a_skill_folder_is_refused(ran):
    with pytest.raises(BackendError, match="skill folder"):
        AppleScriptBackend().run(step("applescript", [], script="x.applescript"), {})
