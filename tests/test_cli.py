"""CLI confirmation tests — ARCHITECTURE.md §9.2, §9.4.

The argument parsing and REPL loop are thin, but `confirm` encodes a
safety rule: only an explicit yes approves.
"""

import builtins

import pytest

from friday.cli import confirm


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", " yes "])
def test_explicit_yes_approves(monkeypatch, answer):
    monkeypatch.setattr(builtins, "input", lambda _: answer)
    assert confirm("open_app(app='Safari')", False) is True


@pytest.mark.parametrize(
    "answer", ["n", "no", "", "maybe", "sure", "ok", "approve", "1"]
)
def test_anything_else_denies(monkeypatch, answer):
    """Only y/yes approves. 'sure' and 'ok' are not yes — a fuzzy match
    here would be a way to approve without meaning to."""
    monkeypatch.setattr(builtins, "input", lambda _: answer)
    assert confirm("open_app(app='Safari')", False) is False


def test_eof_denies(monkeypatch):
    """A closed stdin must not approve (§9.4: timeout denies)."""

    def raise_eof(_):
        raise EOFError

    monkeypatch.setattr(builtins, "input", raise_eof)
    assert confirm("open_app(app='Safari')", False) is False


def test_description_is_printed_in_full(monkeypatch, capsys):
    """No truncation path (§9.2)."""
    long_desc = "send_message(to='Mom', body='%s')" % ("word " * 80)
    monkeypatch.setattr(builtins, "input", lambda _: "n")
    confirm(long_desc, True)
    assert long_desc in capsys.readouterr().out


def test_verbatim_flag_is_visible_in_the_prompt(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", lambda _: "n")
    confirm("x", True)
    assert "verbatim" in capsys.readouterr().out.lower()


def test_main_does_not_shadow_sys_with_a_local_import():
    """A stray `import sys` inside main() made `sys` a local, so any
    non-empty argv crashed with UnboundLocalError further down."""
    from friday.cli import main

    assert "sys" not in main.__code__.co_varnames


# --- friday skill new / validate / dry-run / enable: hardening --------------


@pytest.fixture
def skills_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _skill(*argv):
    from friday.cli import _skill_cli

    return _skill_cli(list(argv))


@pytest.mark.parametrize("bad", ["../../x", "Bad.Id", "a/b", "", "a..b", ".hidden"])
def test_new_rejects_unsafe_ids(skills_cwd, bad):
    assert _skill("new", bad) == 1
    assert not (skills_cwd / "skills").exists()
    assert not (skills_cwd.parent / "x").exists()


def test_new_refuses_to_overwrite_an_existing_skill(skills_cwd):
    assert _skill("new", "my.skill") == 0
    manifest = skills_cwd / "skills" / "my.skill" / "skill.json"
    manifest.write_text('{"keep": "me"}')
    assert _skill("new", "my.skill") == 1
    assert manifest.read_text() == '{"keep": "me"}'


@pytest.mark.parametrize("backend", ["cli", "applescript", "url"])
def test_new_scaffolds_pass_the_validator(skills_cwd, backend):
    from friday.skills.manifest import load_manifest
    from friday.skills.validate import validate_manifest

    assert _skill("new", "my.skill", "--backend", backend) == 0
    skill = load_manifest(skills_cwd / "skills" / "my.skill" / "skill.json")
    assert validate_manifest(skill) == []


def test_new_url_template_dry_run_has_a_real_placeholder(skills_cwd):
    import json as _json

    _skill("new", "my.skill", "--backend", "url")
    data = _json.loads((skills_cwd / "skills" / "my.skill" / "skill.json").read_text())
    assert data["steps"][0]["dry_run"] == "open {url}"


def test_new_applescript_template_creates_its_script(skills_cwd):
    _skill("new", "my.skill", "--backend", "applescript")
    assert (skills_cwd / "skills" / "my.skill" / "skill.applescript").exists()


def test_validate_and_dry_run_report_a_broken_manifest_without_a_traceback(
    skills_cwd, capsys
):
    broken = skills_cwd / "broken"
    broken.mkdir()
    (broken / "skill.json").write_text("{not json")
    assert _skill("validate", str(broken)) == 1
    assert _skill("dry-run", str(broken)) == 1
    assert _skill("validate", str(skills_cwd / "missing")) == 1
    assert "error" in capsys.readouterr().err.lower()


def test_enable_unknown_skill_is_a_clean_error(skills_cwd, monkeypatch, capsys):
    import friday.skills.store as store_mod

    monkeypatch.setenv("FRIDAY_SKILLS_DIR", str(skills_cwd / "none"))
    monkeypatch.setattr(store_mod, "ENABLED_PATH", skills_cwd / "enabled.json")
    assert _skill("enable", "nope") == 1
    assert "unknown skill" in capsys.readouterr().err


# --- main() startup: the path the app runs ------------------------------------------


class _FakeRouter:
    def route(self, transcript):
        raise AssertionError("no input is given, so nothing should be routed")


def test_main_starts_and_exits_cleanly_on_eof(monkeypatch, tmp_path):
    """Regression: the app's core crashed at startup because cli.py read
    Settings fields that settings.py did not define."""
    import friday.cli as cli
    import friday.router as router_mod
    from friday.settings import Settings

    monkeypatch.setattr(router_mod, "Router", _FakeRouter)
    monkeypatch.setattr(Settings, "_path", staticmethod(lambda: tmp_path / "settings.json"))

    def eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", eof)
    assert cli.main(["--log", str(tmp_path / "audit.jsonl")]) == 0
