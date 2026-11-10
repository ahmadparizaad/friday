"""Tests for reminders.add: "remind me to X at T" adds one reminder.

Nothing here touches Reminders or osascript: the applescript backend is
replaced by a fake, and the matcher gets resolver fakes that refuse to run.
"""

import json
from pathlib import Path

import pytest

from friday.policy import ActionClass
from friday.skills.backends.base import BackendResult, fill_argv
from friday.skills.manifest import load_manifest
from friday.skills.matcher import MatchError, Resolvers, match
from friday.skills.runtime import run_skill
from friday.skills.validate import validate_manifest, validate_slots

SKILL_DIR = Path(__file__).parent.parent
SUCCESS = "reminder set: call the bank at 5:00 pm today"


class _Unused:
    """Resolver stand-in: reminders.add has no file/app/contact slots."""

    def resolve(self, text):
        raise AssertionError("resolver must not be called by reminders.add")


NO_RESOLVERS = Resolvers(file=_Unused(), app=_Unused(), contact=_Unused())


class FakeApplescript:
    name = "applescript"

    def __init__(self, output=SUCCESS, ok=True):
        self.output = output
        self.ok = ok
        self.calls = []

    def run(self, step, slots, timeout=15.0):
        self.calls.append(fill_argv(step.argv, slots))
        return BackendResult(self.ok, self.output)


@pytest.fixture
def skill():
    return load_manifest(SKILL_DIR / "skill.json")


def _match(skill, utterance):
    return match(utterance, (skill,), NO_RESOLVERS)


def test_skill_loads_and_validates(skill):
    assert skill.id == "reminders.add"
    assert skill.action_class is ActionClass.MUTATE_LOCAL
    assert set(skill.slots) == {"task", "when"}
    assert skill.slots["task"].max_len == 120
    assert skill.slots["when"].max_len == 40
    assert skill.postcondition.type == "output_matches"
    assert skill.postcondition.value == "^reminder set:"
    assert skill.undo == "delete the reminder in Reminders"
    assert skill.requires.apps == ("Reminders",)
    assert skill.requires.permissions == ("Automation: Reminders",)
    assert validate_manifest(skill) == []


def test_single_applescript_step_with_slot_argv(skill):
    assert [(s.backend, s.script, s.argv) for s in skill.steps] == [
        ("applescript", "add_reminder.applescript", ("{task}", "{when}"))
    ]


def test_examples_all_match(skill):
    for line in (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines():
        utterance = json.loads(line)["u"]
        _match(skill, utterance)  # raises MatchError if not


@pytest.mark.parametrize(
    "utterance, task, when",
    [
        ("remind me to call the bank at 5 pm", "call the bank", "5 pm"),
        ("Remind me to call the bank at 5 pm.", "call the bank", "5 pm"),
        ("remind me to pay rent tomorrow at 9", "pay rent", "tomorrow at 9"),
        ("remind me at 6 to stretch", "stretch", "6"),
        ("Remind me to call mom tomorrow.", "call mom", "tomorrow"),
        ("remind me to take my pills at 17:00", "take my pills", "17:00"),
        ("remind me to call dad at 5:30 pm", "call dad", "5:30 pm"),
        ("remind me to submit the form by 5pm", "submit the form", "5pm"),
        ("remind me to water the plants today at 7", "water the plants", "today at 7"),
        ("remind me at 6:30 pm to stretch.", "stretch", "6:30 pm"),
        ("remind me to meet at the park at 5 pm", "meet at the park", "5 pm"),
    ],
)
def test_positive_matches_fill_exact_slots(skill, utterance, task, when):
    skill_id, slots = _match(skill, utterance)
    assert skill_id == "reminders.add"
    assert slots == {"task": task, "when": when}


@pytest.mark.parametrize(
    "utterance",
    [
        "what's on my calendar today",
        "email the file report to Sarah",
        "set a timer for 5 minutes",
        "remind",
        "remind me",
        "I'm going into a meeting",
        "open safari",
        "search cats",
        "volume up",
        "what time is it",
    ],
)
def test_does_not_claim_other_phrasings(skill, utterance):
    with pytest.raises(MatchError):
        _match(skill, utterance)


def test_slot_too_long_is_rejected(skill):
    assert "SLOT_TOO_LONG" in validate_slots(skill, {"task": "x" * 121, "when": "5 pm"})
    assert "SLOT_TOO_LONG" in validate_slots(skill, {"task": "call", "when": "x" * 41})


def test_leading_dash_slot_is_rejected(skill):
    assert "DASH_LEADING_SLOT" in validate_slots(skill, {"task": "-call", "when": "5 pm"})


def test_valid_slots_pass(skill):
    assert validate_slots(skill, {"task": "call the bank", "when": "5 pm"}) == []


def test_dry_run_names_the_slot_values(skill):
    result = run_skill(
        skill, {"task": "call the bank", "when": "5 pm"}, dry_run=True
    )
    assert result.ok
    assert "call the bank" in result.output
    assert "5 pm" in result.output


def test_run_with_fake_backend_fills_argv_and_passes(skill):
    fake = FakeApplescript()
    result = run_skill(
        skill,
        {"task": "call the bank", "when": "5 pm"},
        backends={"applescript": fake},
    )
    assert result.ok and result.postcondition_ok
    assert fake.calls == [["call the bank", "5 pm"]]


def test_run_with_failing_backend_reports_failure(skill):
    fake = FakeApplescript(output="I didn't understand the time soon", ok=False)
    result = run_skill(
        skill,
        {"task": "call the bank", "when": "soon"},
        backends={"applescript": fake},
    )
    assert not result.ok
    assert fake.calls == [["call the bank", "soon"]]


def test_success_line_without_postcondition_match_fails(skill):
    fake = FakeApplescript(output="done")
    result = run_skill(
        skill,
        {"task": "call the bank", "when": "5 pm"},
        backends={"applescript": fake},
    )
    assert not result.postcondition_ok


@pytest.mark.parametrize(
    "utterance, slots",
    [
        ("remind me to buy milk", {"task": "buy milk", "when": ""}),
        ("Remind me to water the plants.", {"task": "water the plants", "when": ""}),
        ("remind me to call mom tomorrow", {"task": "call mom", "when": "tomorrow"}),
        ("remind me to call the bank at 5 pm", {"task": "call the bank", "when": "5 pm"}),
    ],
)
def test_time_is_optional(skill, utterance, slots):
    assert match(utterance, (skill,)) == ("reminders.add", slots)


def test_script_handles_no_time_and_day_only():
    script = (SKILL_DIR / "add_reminder.applescript").read_text()
    assert 'whenText is in {"", "today", "tomorrow"}' in script
    assert "allday due date" in script
