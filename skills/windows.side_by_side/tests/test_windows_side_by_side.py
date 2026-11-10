"""Tests for windows.side_by_side: put two apps side by side.

The app resolver is always faked, so nothing scans installed apps, and the
AppleScript backend is always faked, so no real window moves.
"""

import json
from pathlib import Path

import pytest

from friday.policy import ActionClass
from friday.skills.backends.base import BackendResult, fill_argv
from friday.skills.manifest import load_manifest
from friday.skills.matcher import MatchError, Resolvers, match
from friday.skills.resolvers import ResolverError
from friday.skills.runtime import run_skill
from friday.skills.validate import validate_manifest, validate_slots

SKILL_DIR = Path(__file__).parent.parent
SUCCESS = "arranged Safari left and Notes right"

KNOWN_APPS = {
    "safari": "Safari",
    "notes": "Notes",
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "slack": "Slack",
    "mail": "Mail",
    "calendar": "Calendar",
}


class FakeApps:
    """Stands in for the app resolver: exact known names only."""

    def resolve(self, text: str) -> str:
        found = KNOWN_APPS.get(text.strip().lower())
        if found is None:
            raise ResolverError(f"no installed application named {text!r}")
        return found


class FakeAppleScript:
    """Records the filled argv and returns a canned result."""

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


@pytest.fixture
def resolvers():
    return Resolvers(app=FakeApps())


def test_skill_loads_and_validates(skill):
    assert skill.id == "windows.side_by_side"
    assert skill.action_class is ActionClass.NAVIGATE
    assert set(skill.slots) == {"left", "right"}
    assert all(s.type == "app" and s.provenance == "resolver" for s in skill.slots.values())
    assert validate_manifest(skill) == []


def test_one_applescript_step_with_the_script(skill):
    assert len(skill.steps) == 1
    step = skill.steps[0]
    assert step.backend == "applescript"
    assert step.script == "side_by_side.applescript"
    assert step.argv == ("{left}", "{right}")
    assert (SKILL_DIR / step.script).is_file()


def test_script_refuses_same_app_and_never_runs_shell():
    text = (SKILL_DIR / "side_by_side.applescript").read_text()
    assert "same app" in text
    assert "do shell script" not in text


def test_examples_all_match(skill, resolvers):
    for line in (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines():
        utterance = json.loads(line)["u"]
        match(utterance, (skill,), resolvers)  # raises MatchError if not


@pytest.mark.parametrize(
    "utterance, left, right",
    [
        ("put Safari and Notes side by side", "Safari", "Notes"),
        ("show Chrome and Slack side by side", "Google Chrome", "Slack"),
        ("split screen Mail and Calendar", "Mail", "Calendar"),
        ("Safari on the left and Notes on the right", "Safari", "Notes"),
        ("Safari on the left and Notes on the right.", "Safari", "Notes"),
        ("Split screen with Mail and Calendar.", "Mail", "Calendar"),
    ],
)
def test_positive_matches_fill_exact_app_names(skill, resolvers, utterance, left, right):
    skill_id, slots = match(utterance, (skill,), resolvers)
    assert skill_id == "windows.side_by_side"
    assert slots == {"left": left, "right": right}


@pytest.mark.parametrize(
    "utterance",
    [
        "open safari",
        "open safari and notes",
        "close tab",
        "put the file on my desktop",
        "make this video vertical",
        "search cats",
        "volume up",
        "what time is it",
        "split screen Mail",
        "put Safari and Notes",
    ],
)
def test_does_not_claim_other_phrasings(skill, resolvers, utterance):
    with pytest.raises(MatchError):
        match(utterance, (skill,), resolvers)


def test_unknown_app_is_a_match_error(skill, resolvers):
    with pytest.raises(MatchError):
        match("put Safari and Fakery side by side", (skill,), resolvers)


def test_too_long_app_name_is_rejected(skill):
    assert "SLOT_TOO_LONG" in validate_slots(
        skill, {"left": "x" * 101, "right": "Notes"}
    )


def test_leading_dash_is_rejected(skill):
    assert "DASH_LEADING_SLOT" in validate_slots(
        skill, {"left": "-Safari", "right": "Notes"}
    )


def test_valid_slots_pass(skill):
    assert validate_slots(skill, {"left": "Safari", "right": "Notes"}) == []


def test_dry_run_names_both_apps(skill):
    result = run_skill(skill, {"left": "Safari", "right": "Notes"}, dry_run=True)
    assert result.ok
    assert "Safari" in result.output and "Notes" in result.output


def test_run_with_fake_backend_succeeds(skill):
    fake = FakeAppleScript()
    result = run_skill(
        skill,
        {"left": "Safari", "right": "Notes"},
        backends={"applescript": fake},
    )
    assert result.ok and result.postcondition_ok
    assert result.output == SUCCESS
    assert fake.calls == [["Safari", "Notes"]]


def test_run_reports_script_failure(skill):
    fake = FakeAppleScript(output="Safari has no open window", ok=False)
    result = run_skill(
        skill,
        {"left": "Safari", "right": "Notes"},
        backends={"applescript": fake},
    )
    assert not result.ok
    assert "no open window" in result.output


def test_unexpected_output_fails_the_postcondition(skill):
    fake = FakeAppleScript(output="done")
    result = run_skill(
        skill,
        {"left": "Safari", "right": "Notes"},
        backends={"applescript": fake},
    )
    assert not result.ok
    assert not result.postcondition_ok
