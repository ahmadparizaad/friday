"""Optional slots (a default for free-text slots) and the requires check."""

import pytest

from friday.skills.manifest import ManifestError, Requires, Skill, Slot, _build
from friday.skills.matcher import MatchError, match
from friday.skills.runtime import missing_requirements

# --- optional slots ---------------------------------------------------------------

REMIND = Skill(
    id="test.remind",
    version="0.1.0",
    title="remind",
    triggers=(r"^remind me to {task} at (?P<when>.+)$", r"^remind me to {task}$"),
    slots={
        "task": Slot(name="task", type="text", provenance="utterance", max_len=50),
        "when": Slot(name="when", type="text", provenance="utterance", max_len=20, default=""),
    },
)


def test_a_trigger_may_leave_out_a_slot_that_has_a_default():
    assert match("remind me to buy milk", (REMIND,)) == (
        "test.remind",
        {"task": "buy milk", "when": ""},
    )


def test_a_spoken_value_still_wins_over_the_default():
    assert match("remind me to buy milk at 5", (REMIND,))[1] == {"task": "buy milk", "when": "5"}


def test_a_slot_without_a_default_is_still_required():
    no_default = Skill(
        id="test.strict",
        version="0.1.0",
        title="strict",
        triggers=(r"^ping$",),
        slots={"who": Slot(name="who", type="text", provenance="utterance", max_len=9)},
    )
    with pytest.raises(MatchError):
        match("ping", (no_default,))


def _manifest(slot_type: str) -> dict:
    return {
        "id": "test.bad_default",
        "version": "0.1.0",
        "title": "t",
        "action_class": "read",
        "triggers": ["^go$"],
        "slots": {"x": {"type": slot_type, "provenance": "resolver", "default": "/etc/passwd"}},
        "steps": [{"id": "s", "backend": "cli", "argv": ["open", "{x}"], "dry_run": "d"}],
        "postcondition": {"type": "output_matches", "value": "."},
        "undo": "none",
    }


@pytest.mark.parametrize("slot_type", ["file", "app", "contact"])
def test_resolved_slots_cannot_have_a_default(slot_type):
    # a default would hand the step a value no resolver ever vouched for
    with pytest.raises(ManifestError):
        _build(_manifest(slot_type))


# --- requires ---------------------------------------------------------------------


def _needs(binaries=(), apps=()):
    return Skill(
        id="test.needs",
        version="0.1.0",
        title="needs",
        triggers=("^x$",),
        requires=Requires(binaries=tuple(binaries), apps=tuple(apps)),
    )


def test_nothing_missing_when_everything_is_installed():
    skill = _needs(["ffmpeg"], ["Mail"])
    assert missing_requirements(skill, which=lambda b: "/bin/" + b, apps=("Mail",)) == []


def test_missing_binary_and_app_are_named():
    skill = _needs(["ffmpeg"], ["Mail"])
    missing = missing_requirements(skill, which=lambda b: None, apps=("Notes",))
    assert missing == ["ffmpeg", "Mail"]


def test_executor_refuses_a_skill_with_missing_requirements(monkeypatch):
    from friday import executor
    from friday.skills import runtime, store

    monkeypatch.setattr(store, "get_enabled", lambda _id: _needs(["ffmpeg"]))
    monkeypatch.setattr(runtime, "missing_requirements", lambda skill: ["ffmpeg"])
    ran = []
    monkeypatch.setattr(runtime, "run_skill", lambda *a, **k: ran.append(1))
    with pytest.raises(executor.ExecutionError, match="ffmpeg"):
        executor._run_skill({"skill_id": "test.needs", "slots": {}})
    assert ran == []
