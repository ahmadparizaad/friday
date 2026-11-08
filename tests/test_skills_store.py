"""Store tests — the validator gates enablement (review fix C1)."""

import json

import pytest

from friday.skills import store

VALID = {
    "id": "t.ok",
    "version": "0.1.0",
    "title": "ok",
    "triggers": ["do {name}"],
    "slots": {"name": {"type": "text", "provenance": "utterance", "max_len": 20}},
    "action_class": "navigate",
    "requires": {"binaries": [], "apps": [], "permissions": []},
    "allowed_dirs": ["~/Downloads"],
    "steps": [
        {"id": "s", "backend": "cli", "argv": ["mdfind", "{name}"], "dry_run": "x"}
    ],
    "postcondition": {"type": "output_matches", "value": "."},
    "undo": "none",
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("FRIDAY_SKILLS_DIR", str(skills))
    monkeypatch.setattr(store, "ENABLED_PATH", tmp_path / "skills.json")
    return skills


def _write(root, manifest):
    d = root / manifest["id"]
    d.mkdir()
    (d / "skill.json").write_text(json.dumps(manifest))


def test_valid_skill_enables(env):
    _write(env, VALID)
    store.enable("t.ok")
    assert [s.id for s in store.enabled_skills()] == ["t.ok"]


def test_enable_refuses_a_skill_the_validator_rejects(env):
    bad = {**VALID, "id": "t.bad", "action_class": "read",
           "steps": [{"id": "s", "backend": "cli", "argv": ["rm", "x"], "dry_run": "x"}]}
    _write(env, bad)
    with pytest.raises(store.StoreError, match="ACTION_CLASS_TOO_LOW|BINARY_NOT_ALLOWED"):
        store.enable("t.bad")


def test_invalid_skill_already_in_enabled_set_is_not_returned(env):
    bad = {**VALID, "id": "t.bad", "steps": [
        {"id": "s", "backend": "cli", "argv": ["osascript", "{name}"], "dry_run": "x"}]}
    _write(env, bad)
    store.ENABLED_PATH.write_text(json.dumps({"enabled": ["t.bad"]}))
    assert store.enabled_skills() == ()


# --- review fixes: identity pinning, enabled-aware lookup -------------------


def test_editing_an_enabled_skill_disables_it_until_re_enabled(env):
    _write(env, VALID)
    store.enable("t.ok")
    path = env / "t.ok" / "skill.json"
    path.write_text(json.dumps({**VALID, "version": "0.2.0"}))
    assert store.enabled_skills() == ()
    store.enable("t.ok")
    assert [s.version for s in store.enabled_skills()] == ["0.2.0"]


def test_editing_a_skill_script_invalidates_the_pin(env):
    _write(env, VALID)
    script = env / "t.ok" / "x.applescript"
    script.write_text("on run argv\nend run")
    store.enable("t.ok")
    script.write_text('on run argv\ndo shell script "id"\nend run')
    assert store.enabled_skills() == ()


def test_entry_without_a_pin_is_not_enabled(env):
    _write(env, VALID)
    store.ENABLED_PATH.write_text(json.dumps({"enabled": ["t.ok"]}))
    assert store.enabled_skills() == ()


def test_get_enabled_only_returns_pinned_enabled_skills(env):
    _write(env, VALID)
    assert store.get_enabled("t.ok") is None
    store.enable("t.ok")
    assert store.get_enabled("t.ok").id == "t.ok"
    store.disable("t.ok")
    assert store.get_enabled("t.ok") is None


def test_nested_script_changes_invalidate_the_pin(env):
    _write(env, VALID)
    nested = env / "t.ok" / "scripts"
    nested.mkdir()
    script = nested / "x.applescript"
    script.write_text("on run argv\nend run")
    store.enable("t.ok")
    assert [s.id for s in store.enabled_skills()] == ["t.ok"]
    script.write_text('on run argv\ndo shell script "id"\nend run')
    assert store.enabled_skills() == ()


def test_editing_tests_or_examples_does_not_invalidate_the_pin(env):
    _write(env, VALID)
    root = env / "t.ok"
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("a")
    (root / "examples.jsonl").write_text("{}")
    store.enable("t.ok")
    (root / "tests" / "test_x.py").write_text("b")
    (root / "examples.jsonl").write_text('{"u": "new"}')
    assert [s.id for s in store.enabled_skills()] == ["t.ok"]


def test_a_script_under_tests_or_named_like_an_example_is_still_pinned_or_rejected(env):
    """Exclusions must not let an executable file escape the pin: the
    validator rejects scripts that live in unpinned places."""
    from friday.skills.manifest import load_manifest
    from friday.skills.validate import SCRIPT_NOT_ALLOWED, validate_manifest

    for script in ("tests/helper.applescript", "examples.jsonl", "run.sh",
                   "__pycache__/x.applescript"):
        manifest = {**VALID, "id": "t.scr", "steps": [
            {"id": "s", "backend": "applescript", "script": script,
             "argv": ["{name}"], "dry_run": "x"}]}
        d = env / "t.scr"
        d.mkdir(exist_ok=True)
        (d / "skill.json").write_text(json.dumps(manifest))
        assert SCRIPT_NOT_ALLOWED in validate_manifest(load_manifest(d / "skill.json")), script


def test_digest_is_length_framed(env):
    """Moving bytes between two files must change the digest."""
    _write(env, VALID)
    root = env / "t.ok"
    (root / "a.applescript").write_text("AA")
    (root / "b.applescript").write_text("B")
    d1 = store.skill_digest(store.get("t.ok"))
    (root / "a.applescript").write_text("A")
    (root / "b.applescript").write_text("AB")
    assert store.skill_digest(store.get("t.ok")) != d1
