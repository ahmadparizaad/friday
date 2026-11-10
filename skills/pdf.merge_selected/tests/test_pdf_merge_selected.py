"""Tests for pdf.merge_selected: merge the PDFs selected in Finder into one file."""

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from friday.policy import ActionClass
from friday.skills.backends.base import BackendResult
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
SUCCESS = "merged 2 PDFs into /Users/someone/Downloads/Merged 2026-11-11.pdf"


@pytest.fixture
def skill():
    return load_manifest(SKILL_DIR / "skill.json")


class FakeApplescript:
    """Stands in for the applescript backend; never runs osascript."""

    name = "applescript"

    def __init__(self, output: str, ok: bool = True):
        self.output = output
        self.ok = ok
        self.calls = []

    def run(self, step, slots, timeout=15.0):
        self.calls.append((step, dict(slots)))
        return BackendResult(ok=self.ok, output=self.output)


def test_skill_loads_and_validates(skill):
    assert skill.id == "pdf.merge_selected"
    assert skill.action_class is ActionClass.MUTATE_LOCAL
    assert dict(skill.slots) == {}
    assert validate_manifest(skill) == []


def test_single_applescript_step_runs_selection_mode(skill):
    assert len(skill.steps) == 1
    step = skill.steps[0]
    assert step.backend == "applescript"
    assert step.script == "merge_pdfs.applescript"
    assert step.argv == ("selection",)


def test_script_never_shells_out():
    source = (SKILL_DIR / "merge_pdfs.applescript").read_text()
    assert "do shell script" not in source
    assert 'use framework "Quartz"' in source


def test_examples_all_match(skill):
    lines = (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines()
    assert len(lines) >= 12
    for line in lines:
        utterance = json.loads(line)["u"]
        assert match(utterance, (skill,))[0] == "pdf.merge_selected"


@pytest.mark.parametrize(
    "utterance",
    [
        "merge these PDFs",
        "combine the selected PDFs",
        "merge the selected files into one PDF",
        "join these PDFs.",
    ],
)
def test_positive_phrasings_match(skill, utterance):
    skill_id, slots = match(utterance, (skill,))
    assert skill_id == "pdf.merge_selected"
    assert slots == {}


@pytest.mark.parametrize(
    "utterance",
    [
        "open the file budget",  # files.find_open
        "make these images web ready",  # images.web_ready
        "merge my downloads",  # must name PDFs or files
        "email the file report to Sarah",  # mail.draft_attachment
        "clean up my downloads",  # files.tidy_downloads
        "open safari",  # built-in
        "search cats",  # built-in
        "volume up",  # built-in
        "what time is it",  # built-in
        "merge these PDFs into a new folder",  # not a whole-utterance match
    ],
)
def test_does_not_claim_other_phrasings(skill, utterance):
    with pytest.raises(MatchError):
        match(utterance, (skill,))


def test_slot_validation_rules_apply_to_text_slots(skill):
    # The skill itself has no slots, so the generic checks are exercised on a
    # copy of the manifest that declares one text slot.
    with_slot = replace(
        skill,
        slots=MappingProxyType(
            {"text": Slot(name="text", type="text", provenance="utterance", max_len=10)}
        ),
    )
    assert validate_slots(with_slot, {"text": "short"}) == []
    assert SLOT_TOO_LONG in validate_slots(with_slot, {"text": "x" * 11})
    assert DASH_LEADING_SLOT in validate_slots(with_slot, {"text": "-rf"})


def test_dry_run_describes_the_merge(skill):
    result = run_skill(skill, {}, dry_run=True)
    assert result.ok
    assert "merge the PDFs selected in Finder" in result.output
    assert "Merged PDF" in result.output


def test_run_with_fake_backend_succeeds(skill):
    fake = FakeApplescript(SUCCESS)
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert result.ok and result.postcondition_ok
    assert result.output == SUCCESS
    (step, slots), = fake.calls
    assert step.argv == ("selection",)
    assert slots == {}


def test_run_reports_failure_when_script_fails(skill):
    fake = FakeApplescript("select at least two PDFs in Finder", ok=False)
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


def test_unexpected_success_line_fails_postcondition(skill):
    fake = FakeApplescript("done")
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok
