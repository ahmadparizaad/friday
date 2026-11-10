"""Tests for files.tidy_downloads: "clean up my downloads" sorts loose files into subfolders.

The AppleScript itself is never run here (it would move real files). The
tests check the manifest, the trigger, the dry run, and the argv and
output contract using a fake applescript backend.
"""

import dataclasses
import json
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
SCRIPT = SKILL_DIR / "tidy_downloads.applescript"
SUCCESS = "moved 3 files into folders"


class FakeApplescript:
    name = "applescript"

    def __init__(self, output: str = SUCCESS, ok: bool = True):
        self.output = output
        self.ok = ok
        self.calls: list[tuple] = []

    def run(self, step, slots, timeout=15.0):
        self.calls.append((step.script, step.argv, dict(slots)))
        return BackendResult(self.ok, self.output)


@pytest.fixture
def skill():
    return load_manifest(SKILL_DIR / "skill.json")


def test_skill_loads_and_validates(skill):
    assert skill.id == "files.tidy_downloads"
    assert skill.action_class is ActionClass.MUTATE_LOCAL
    assert len(skill.slots) == 0
    assert validate_manifest(skill) == []


def test_undo_says_nothing_was_deleted(skill):
    assert "nothing was deleted" in skill.undo
    assert "dragged back" in skill.undo


def test_requires_finder_automation(skill):
    assert "Automation: Finder" in skill.requires.permissions


def test_allowed_dirs_is_downloads_only(skill):
    assert skill.allowed_dirs == ("~/Downloads",)


def test_single_applescript_step_with_run_argv(skill):
    assert len(skill.steps) == 1
    step = skill.steps[0]
    assert step.backend == "applescript"
    assert step.script == "tidy_downloads.applescript"
    assert step.argv == ("run",)


def test_postcondition_matches_the_success_line(skill):
    assert skill.postcondition.type == "output_matches"
    assert skill.postcondition.value == r"^moved \d+ files into folders(, skipped \d+ that could not be moved)?$"


def test_script_never_shells_out_or_deletes():
    code = [
        line
        for line in SCRIPT.read_text().splitlines()
        if not line.lstrip().startswith("--")
    ]
    text = "\n".join(code)
    assert 'tell application "Finder"' in text
    assert "do shell script" not in text
    assert "delete" not in text.lower()


def test_examples_all_match(skill):
    for line in (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines():
        utterance = json.loads(line)["u"]
        match(utterance, (skill,))  # raises MatchError if not


@pytest.mark.parametrize(
    "utterance",
    [
        "clean up my downloads",
        "Clean up my downloads.",
        "tidy my downloads folder",
        "organise my downloads",
        "organize my downloads!",
        "sort my downloads",
    ],
)
def test_phrasings_match_with_no_slots(skill, utterance):
    skill_id, slots = match(utterance, (skill,))
    assert skill_id == "files.tidy_downloads"
    assert slots == {}


@pytest.mark.parametrize(
    "utterance",
    [
        "clean up my desktop",
        "open downloads",
        "delete my downloads",
        "open safari",
        "search cats",
        "volume up",
        "what time is it",
        "open the file budget",
        "find my file called resume",
        "merge these PDFs",
        "please clean up my desktop downloads and email them",
    ],
)
def test_does_not_claim_other_phrasings(skill, utterance):
    with pytest.raises(MatchError):
        match(utterance, (skill,))


def test_slot_validation_with_no_declared_slots(skill):
    assert validate_slots(skill, {}) == []


def test_slot_codes_are_reported_for_a_declared_slot(skill):
    # The skill declares no slots, so the validator's codes are checked on
    # a copy of the manifest with one probe text slot added.
    probe = Slot(name="probe", type="text", provenance="utterance", max_len=5)
    probed = dataclasses.replace(skill, slots=MappingProxyType({"probe": probe}))
    assert SLOT_TOO_LONG in validate_slots(probed, {"probe": "123456"})
    assert DASH_LEADING_SLOT in validate_slots(probed, {"probe": "-rf"})


def test_dry_run_names_the_downloads_folder_and_subfolders(skill):
    result = run_skill(skill, {}, dry_run=True)
    assert result.ok
    assert "~/Downloads" in result.output
    assert "Images" in result.output and "Installers" in result.output


def test_run_sends_run_argv_to_the_applescript_backend(skill):
    fake = FakeApplescript()
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert result.ok and result.postcondition_ok
    assert result.output == SUCCESS
    assert fake.calls == [("tidy_downloads.applescript", ("run",), {})]


def test_zero_moves_is_still_a_success(skill):
    fake = FakeApplescript(output="moved 0 files into folders")
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert result.ok and result.postcondition_ok


def test_script_failure_fails_the_skill(skill):
    fake = FakeApplescript(output="execution error: Finder got an error", ok=False)
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


def test_unexpected_output_fails_the_postcondition(skill):
    fake = FakeApplescript(output="all done")
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


@pytest.mark.parametrize(
    "line, ok",
    [
        ("moved 12 files into folders", True),
        ("moved 12 files into folders, skipped 1 that could not be moved", True),
        ("moved 12 files into folders, deleted 3", False),
    ],
)
def test_postcondition_accepts_a_skipped_count(line, ok):
    import re

    value = load_manifest(SKILL_DIR / "skill.json").postcondition.value
    assert (re.search(value, line) is not None) is ok
