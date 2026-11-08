"""Runtime tests — docs/PLAN-SKILLS.md (S1, T4).

The runtime contract: dry-run touches nothing, a failing postcondition
fails the skill, a delete moves to the Bin with a restore record, and a
timeout kills the subprocess. Backends are faked so the tests assert the
runtime's logic, not the real system.
"""

import json
from pathlib import Path

from friday.policy import ActionClass
from friday.skills.backends.base import BackendResult
from friday.skills.manifest import Postcondition, Requires, Skill, Slot, Step
from friday.skills.runtime import SkillResult, run_skill


class FakeBackend:
    """Returns a canned result per step id."""

    name = "fake"

    def __init__(self, results):
        self.results = results
        self.calls = []

    def run(self, step, slots, timeout):
        self.calls.append((step.id, dict(slots), timeout))
        ok, output = self.results.get(step.id, (True, ""))
        return BackendResult(ok=ok, output=output)


class TimeoutBackend:
    name = "timeout"

    def run(self, step, slots, timeout):
        from friday.skills.backends.base import BackendError

        raise BackendError(f"{step.id} timed out")


def make_skill(**kw):
    base = dict(
        id="test.skill",
        version="0.1.0",
        title="Test",
        triggers=("do {name}",),
        slots={
            "name": Slot(name="name", type="text", provenance="utterance"),
        },
        action_class=ActionClass.NAVIGATE,
        requires=Requires(),
        allowed_dirs=("~/Downloads",),
        steps=(
            Step(
                id="act",
                backend="fake",
                argv=("echo", "{name}"),
                dry_run="do {name}",
            ),
        ),
        postcondition=Postcondition(type="output_matches", value="."),
        undo="none",
    )
    base.update(kw)
    return Skill(**base)


def snapshot(root: Path) -> dict:
    """A byte-level snapshot of a tree, for dry-run assertions."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_file():
            out[rel] = path.read_bytes()
        else:
            out[rel] = b"<dir>"
    return out


# --- dry run touches nothing ----------------------------------------------


def test_dry_run_leaves_tree_byte_identical(tmp_path):
    (tmp_path / "file.txt").write_text("content")
    before = snapshot(tmp_path)
    skill = make_skill()
    result = run_skill(skill, {"name": "file.txt"}, dry_run=True)
    assert result.ok
    assert result.postcondition_ok
    assert "do file.txt" in result.output
    assert snapshot(tmp_path) == before


def test_dry_run_reports_each_step(tmp_path):
    skill = make_skill(
        steps=(
            Step(id="a", backend="fake", argv=("echo", "{name}"), dry_run="first {name}"),
            Step(id="b", backend="fake", argv=("echo", "{name}"), dry_run="second {name}"),
        )
    )
    result = run_skill(skill, {"name": "x"}, dry_run=True)
    assert result.ok
    assert "first x" in result.output
    assert "second x" in result.output


# --- failing postcondition fails the skill --------------------------------


def test_failing_postcondition_fails_the_skill():
    backend = FakeBackend({"act": (True, "done")})
    skill = make_skill(
        postcondition=Postcondition(type="output_matches", value="NOMATCH")
    )
    result = run_skill(skill, {"name": "x"}, backends={"fake": backend})
    assert not result.ok
    assert not result.postcondition_ok

# --- delete moves to the Bin with a restore record ------------------------


def test_delete_moves_to_bin_with_restore_record(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("old")
    trash, state = tmp_path / "Trash", tmp_path / "state"
    backend = FakeBackend({"act": (True, str(target))})
    skill = make_skill(
        action_class=ActionClass.DESTRUCTIVE,
        allowed_dirs=(str(tmp_path),),
        steps=(
            Step(
                id="act",
                backend="fake",
                argv=("echo", "{name}"),
                dry_run="delete {name}",
                delete=True,
            ),
        ),
        postcondition=Postcondition(type="output_matches", value="."),
    )
    result = run_skill(
        skill,
        {"name": "old.txt"},
        backends={"fake": backend},
        trash_dir=trash,
        state_dir=state,
    )
    assert result.ok
    assert not target.exists(), "file must be gone from its original location"
    assert (trash / "old.txt").exists(), "file must be in the Bin"
    (record_file,) = state.glob("*.json")
    record = json.loads(record_file.read_text())
    assert record["original"] == str(target)
    assert record["trashed"] == str(trash / "old.txt")


# --- timeout kills the subprocess -----------------------------------------


def test_timeout_fails_the_step_and_stops():
    skill = make_skill()
    result = run_skill(skill, {"name": "x"}, backends={"fake": TimeoutBackend()})
    assert not result.ok
    assert "timed out" in result.output
    assert result.steps[0].ok is False


def test_step_failure_stops_the_skill():
    backend = FakeBackend({"act": (False, "boom")})
    skill = make_skill()
    result = run_skill(skill, {"name": "x"}, backends={"fake": backend})
    assert not result.ok
    assert "boom" in result.output


# --- slot validation ------------------------------------------------------


def test_invalid_slots_refuse_before_any_step():
    backend = FakeBackend({"act": (True, "done")})
    skill = make_skill(
        slots={
            "name": Slot(
                name="name", type="text", provenance="utterance", max_len=3
            ),
        }
    )
    result = run_skill(skill, {"name": "toolong"}, backends={"fake": backend})
    assert not result.ok
    assert backend.calls == [], "no step may run with invalid slots"


# --- frontmost_app_is postcondition ---------------------------------------


def test_frontmost_app_postcondition():
    backend = FakeBackend({"act": (True, "ok")})
    skill = make_skill(
        postcondition=Postcondition(type="frontmost_app_is", value="Preview|Finder")
    )
    result = run_skill(
        skill,
        {"name": "x"},
        backends={"fake": backend},
        frontmost_app=lambda: "Preview",
    )
    assert result.ok


def test_frontmost_app_postcondition_failure():
    backend = FakeBackend({"act": (True, "ok")})
    skill = make_skill(
        postcondition=Postcondition(type="frontmost_app_is", value="Preview")
    )
    result = run_skill(
        skill,
        {"name": "x"},
        backends={"fake": backend},
        frontmost_app=lambda: "Safari",
    )
    assert not result.ok


# --- review fixes: step chaining, Bin safety -------------------------------


def _chain_skill(allowed):
    return make_skill(
        allowed_dirs=(str(allowed),),
        steps=(
            Step(id="find", backend="fake", argv=("echo", "{name}"), dry_run="find"),
            Step(id="open", backend="fake", argv=("echo", "{result}"), dry_run="open"),
        ),
    )


def test_result_slot_is_first_output_line_inside_allowed_dirs(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    wanted = ok / "invoice.pdf"
    wanted.write_text("x")
    backend = FakeBackend(
        {
            "find": (True, f"/etc/hosts\n{wanted}\n{ok}/other.pdf"),
            "open": (True, "opened"),
        }
    )
    result = run_skill(_chain_skill(ok), {"name": "invoice"}, backends={"fake": backend})
    assert result.ok
    assert backend.calls[1][1]["result"] == str(wanted)


def test_no_match_inside_allowed_dirs_stops_before_the_next_step(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    backend = FakeBackend({"find": (True, "/etc/hosts")})
    result = run_skill(_chain_skill(ok), {"name": "x"}, backends={"fake": backend})
    assert not result.ok
    assert [c[0] for c in backend.calls] == ["find"]


def test_executable_result_is_never_opened(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    bad = ok / "run.command"
    bad.write_text("x")
    backend = FakeBackend({"find": (True, str(bad))})
    result = run_skill(_chain_skill(ok), {"name": "x"}, backends={"fake": backend})
    assert not result.ok
    assert [c[0] for c in backend.calls] == ["find"]


def _delete_skill(allowed):
    return make_skill(
        action_class=ActionClass.DESTRUCTIVE,
        allowed_dirs=(str(allowed),),
        steps=(
            Step(id="act", backend="fake", argv=("echo", "{name}"),
                 dry_run="delete", delete=True),
        ),
    )


def test_trash_never_overwrites_and_keeps_every_restore_record(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    trash, state = tmp_path / "Trash", tmp_path / "state"
    skill = _delete_skill(ok)
    for content in ("first", "second"):
        f = ok / "same.txt"
        f.write_text(content)
        backend = FakeBackend({"act": (True, str(f))})
        assert run_skill(skill, {"name": "n"}, backends={"fake": backend},
                         trash_dir=trash, state_dir=state).ok
    assert sorted(p.read_text() for p in trash.iterdir()) == ["first", "second"]
    assert len(list(state.glob("*.json"))) == 2


def test_delete_outside_allowed_dirs_is_refused_and_file_untouched(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    victim = tmp_path / "elsewhere.txt"
    victim.write_text("keep")
    backend = FakeBackend({"act": (True, str(victim))})
    result = run_skill(_delete_skill(ok), {"name": "n"}, backends={"fake": backend},
                       trash_dir=tmp_path / "Trash", state_dir=tmp_path / "state")
    assert not result.ok
    assert victim.read_text() == "keep"


def test_delete_with_no_existing_target_fails_loudly(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    backend = FakeBackend({"act": (True, str(ok / "ghost.txt"))})
    result = run_skill(_delete_skill(ok), {"name": "n"}, backends={"fake": backend},
                       trash_dir=tmp_path / "Trash", state_dir=tmp_path / "state")
    assert not result.ok
