"""Tests for images.web_ready: make the selected images web ready.

No test touches Finder or the real script: the AppleScript backend is faked.
"""

import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from friday.policy import ActionClass
from friday.skills.backends import BackendResult
from friday.skills.manifest import Slot, load_manifest
from friday.skills.matcher import MatchError, match
from friday.skills.runtime import run_skill
from friday.skills.validate import (
    DASH_LEADING_SLOT,
    SLOT_TOO_LONG,
    validate_manifest,
    validate_slots,
)

SKILL_DIR = Path(__file__).parent.parent
SUCCESS = "made 2 web-ready images"


@pytest.fixture
def skill():
    return load_manifest(SKILL_DIR / "skill.json")


class FakeAppleScript:
    name = "applescript"

    def __init__(self, ok=True, output=SUCCESS):
        self.ok = ok
        self.output = output
        self.calls = []

    def run(self, step, slots, timeout=15.0):
        self.calls.append((step.script, list(step.argv), dict(slots)))
        return BackendResult(ok=self.ok, output=self.output)


def test_skill_loads_and_validates(skill):
    assert skill.id == "images.web_ready"
    assert skill.action_class is ActionClass.MUTATE_LOCAL
    assert dict(skill.slots) == {}
    assert validate_manifest(skill) == []


def test_runs_only_the_selection_mode_of_its_script(skill):
    assert [s.backend for s in skill.steps] == ["applescript"]
    assert [list(s.argv) for s in skill.steps] == [["selection"]]
    assert skill.steps[0].script == "web_ready.applescript"
    assert (SKILL_DIR / "web_ready.applescript").is_file()


def test_allowed_folders_and_undo_are_declared(skill):
    assert set(skill.allowed_dirs) == {
        "~/Downloads",
        "~/Documents",
        "~/Desktop",
        "~/Pictures",
    }
    assert skill.undo == "delete the new -web.jpg files; originals are unchanged"


def test_examples_all_match(skill):
    for line in (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines():
        utterance = json.loads(line)["u"]
        skill_id, slots = match(utterance, (skill,))
        assert skill_id == "images.web_ready" and slots == {}


@pytest.mark.parametrize(
    "utterance",
    [
        "make these images web ready",
        "Make these images web ready.",
        "shrink these photos",
        "make the selected pictures smaller",
        "resize these images for the web",
        "convert these photos to JPEG",
        "convert these photos to jpg.",
    ],
)
def test_positive_phrasings_match(skill, utterance):
    skill_id, slots = match(utterance, (skill,))
    assert skill_id == "images.web_ready" and slots == {}


@pytest.mark.parametrize(
    "utterance",
    [
        # built-in phrasings
        "open safari",
        "search cats",
        "volume up",
        "what time is it",
        "take a screenshot",
        # the nearest phrasings of other skills
        "merge these PDFs",
        "make this video vertical",
        "open the file photo",
        "clean up my downloads",
        "put Safari and Notes side by side",
        # not a web-ready request
        "make these images",
        "convert these photos to PDF",
    ],
)
def test_does_not_claim_other_phrasings(skill, utterance):
    with pytest.raises(MatchError):
        match(utterance, (skill,))


def test_slot_validation_rules_still_apply(skill):
    # The skill has no slots of its own, so the two rules are checked on a
    # copy with a text slot: the validator must still refuse long and
    # dash-leading values.
    text_slot = Slot(name="x", type="text", provenance="utterance", max_len=5)
    with_slot = replace(skill, slots=MappingProxyType({"x": text_slot}))
    assert validate_slots(with_slot, {"x": "short"}) == []
    assert SLOT_TOO_LONG in validate_slots(with_slot, {"x": "far too long"})
    assert DASH_LEADING_SLOT in validate_slots(with_slot, {"x": "-rf"})
    assert validate_slots(skill, {}) == []


def test_dry_run_names_the_result_files(skill):
    result = run_skill(skill, {}, dry_run=True)
    assert result.ok
    assert "1600" in result.output
    assert "-web.jpg" in result.output


def test_run_with_fake_backend_succeeds(skill):
    fake = FakeAppleScript()
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert result.ok and result.postcondition_ok
    assert fake.calls == [("web_ready.applescript", ["selection"], {})]


def test_script_failure_line_fails_the_skill(skill):
    fake = FakeAppleScript(ok=False, output="select some images in Finder")
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert result.output == "select some images in Finder"


def test_unexpected_output_fails_the_postcondition(skill):
    fake = FakeAppleScript(ok=True, output="done")
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok
