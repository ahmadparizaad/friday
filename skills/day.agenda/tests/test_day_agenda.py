"""Tests for day.agenda: read-only summary of today's events and reminders.

The AppleScript is never run here. Its backend is replaced by a fake that
records the argv it was given and returns a canned line.
"""

import json
from pathlib import Path

import pytest

from friday.skills.backends import BackendResult
from friday.skills.backends.base import fill_argv
from friday.skills.manifest import load_manifest
from friday.skills.matcher import MatchError, match
from friday.skills.runtime import run_skill
from friday.skills.validate import validate_manifest, validate_slots

SKILL_DIR = Path(__file__).parent.parent
SUCCESS_LINE = "You have 3 events today: 10:00 Standup, 13:30 Lunch with Ana, 16:00 Design review. 2 reminders due: call the bank, pay rent."
CLEAR_LINE = "Your calendar is clear today and no reminders are due."


class FakeAppleScript:
    name = "applescript"

    def __init__(self, output=SUCCESS_LINE, ok=True):
        self.output = output
        self.ok = ok
        self.calls = []

    def run(self, step, slots, timeout=15.0):
        self.calls.append(
            {
                "script": step.script,
                "argv": fill_argv(step.argv, slots),
                "timeout": timeout,
            }
        )
        return BackendResult(self.ok, self.output)


@pytest.fixture
def skill():
    return load_manifest(SKILL_DIR / "skill.json")


def test_skill_loads_and_validates(skill):
    assert skill.id == "day.agenda"
    assert skill.action_class.value == "read"
    assert dict(skill.slots) == {}
    assert validate_manifest(skill) == []


def test_uses_only_the_applescript_backend_and_one_step(skill):
    assert [s.backend for s in skill.steps] == ["applescript"]
    assert skill.steps[0].script == "agenda.applescript"
    assert skill.steps[0].argv == ("today",)


def test_requires_calendar_and_reminders_only(skill):
    assert set(skill.requires.apps) == {"Calendar", "Reminders"}
    assert set(skill.requires.permissions) == {
        "Automation: Calendar",
        "Automation: Reminders",
    }


def test_undo_is_none_and_postcondition_is_the_speakable_opening(skill):
    assert skill.undo == "none"
    assert skill.postcondition.type == "output_matches"
    assert skill.postcondition.value == "^(You have|Your calendar is clear)"


def test_script_exists_and_is_not_under_tests(skill):
    script = SKILL_DIR / "agenda.applescript"
    assert script.is_file()
    assert "tests" not in script.relative_to(SKILL_DIR).parts


def test_slot_validation_has_nothing_to_check(skill):
    # The skill declares no slots, so there is nothing for the validator to
    # reject; the argv is fixed and no value is ever substituted into it.
    assert validate_slots(skill, {}) == []


def test_examples_all_match(skill):
    lines = (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines()
    assert 12 <= len(lines) <= 20
    for line in lines:
        utterance = json.loads(line)["u"]
        skill_id, slots = match(utterance, (skill,))
        assert skill_id == "day.agenda" and slots == {}


@pytest.mark.parametrize(
    "utterance",
    [
        "what's on my calendar today",
        "What's on my calendar today?",
        "what's my day look like",
        "What's my day look like.",
        "what do I have today",
        "read my agenda",
        "what's on today",
    ],
)
def test_positive_phrasings_match(skill, utterance):
    skill_id, slots = match(utterance, (skill,))
    assert skill_id == "day.agenda" and slots == {}


@pytest.mark.parametrize(
    "utterance",
    [
        "what time is it",  # built-in clock
        "remind me to call the bank",  # reminders.add
        "remind me to call the bank at 5 pm",  # reminders.add
        "I'm going into a meeting",  # focus.meeting
        "open calendar",  # built-in app open
        "open safari",  # built-in app open
        "search cats",  # built-in search
        "volume up",  # built-in volume
        "what's on my calendar tomorrow",  # not today
        "what do I have to do today",  # a task question, not the agenda
        "read my email",
        "what's on my screen",
    ],
)
def test_negative_phrases_do_not_match(skill, utterance):
    with pytest.raises(MatchError):
        match(utterance, (skill,))


def test_dry_run_reports_the_step(skill):
    result = run_skill(skill, {}, dry_run=True)
    assert result.ok
    assert "calendar" in result.output and "reminders" in result.output


def test_run_with_fake_backend_succeeds_and_passes_the_fixed_argv(skill):
    fake = FakeAppleScript()
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert result.ok and result.postcondition_ok
    assert result.output == SUCCESS_LINE
    assert fake.calls == [
        {"script": "agenda.applescript", "argv": ["today"], "timeout": 15.0}
    ]


def test_clear_day_output_also_passes_the_postcondition(skill):
    fake = FakeAppleScript(output=CLEAR_LINE)
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert result.ok and result.postcondition_ok


def test_unexpected_output_fails_the_postcondition(skill):
    fake = FakeAppleScript(output="Something went wrong")
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


def test_script_failure_line_fails_the_skill(skill):
    fake = FakeAppleScript(output="execution error: Calendar got an error", ok=False)
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert "Calendar got an error" in result.output
