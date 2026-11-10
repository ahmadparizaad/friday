"""Tests for files.find_open: "open the file X" / "find the document X and open it".

Nothing here touches Spotlight, Finder or the user's files: the file
resolver is a fake, and the applescript backend is a fake.
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
SCRIPT = SKILL_DIR / "open_document.applescript"
RESOLVED_DIR = "/Users/test/Documents"


class FakeFile:
    """Resolves any name to one path under Documents; records each call."""

    def __init__(self):
        self.calls = []

    def resolve(self, text):
        self.calls.append(text)
        return f"{RESOLVED_DIR}/{text}"


class AmbiguousFile:
    """Spotlight found several matches: the resolver refuses."""

    def resolve(self, text):
        raise ResolverError(f"{text!r} matches several files")


class Unused:
    """Stands in for the app and contact resolvers, which this skill never uses."""

    def resolve(self, text):
        raise AssertionError("this skill must not resolve apps or contacts")


def resolvers(file_resolver=None):
    return Resolvers(file=file_resolver or FakeFile(), app=Unused(), contact=Unused())


class FakeApplescript:
    name = "applescript"

    def __init__(self, output):
        self.output = output
        self.calls = []

    def run(self, step, slots, timeout=15.0):
        self.calls.append((step, fill_argv(step.argv, slots)))
        return BackendResult(True, self.output)


@pytest.fixture
def skill():
    return load_manifest(SKILL_DIR / "skill.json")


def test_skill_loads_and_validates(skill):
    assert skill.id == "files.find_open"
    assert skill.action_class is ActionClass.NAVIGATE
    assert set(skill.slots) == {"file"}
    assert skill.slots["file"].type == "file"
    assert skill.slots["file"].provenance == "resolver"
    assert validate_manifest(skill) == []


def test_one_applescript_step_runs_the_script_with_the_file_slot(skill):
    (step,) = skill.steps
    assert step.backend == "applescript"
    assert step.script == "open_document.applescript"
    assert step.argv == ("{file}",)
    assert SCRIPT.is_file()


def test_undo_and_postcondition_are_declared(skill):
    assert skill.undo == "close the document"
    assert skill.postcondition.type == "output_matches"
    assert skill.postcondition.value == "^opened .+"


def test_allowed_dirs_are_the_three_user_folders(skill):
    assert skill.allowed_dirs == ("~/Downloads", "~/Documents", "~/Desktop")


def test_examples_all_match(skill):
    for line in (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines():
        utterance = json.loads(line)["u"]
        match(utterance, (skill,), resolvers())  # raises MatchError if not


@pytest.mark.parametrize(
    "utterance, name",
    [
        ("open the file budget", "budget"),
        ("open my file called resume", "resume"),
        ("find the file named invoice and open it", "invoice"),
        ("open the document tax return", "tax return"),
        ("Open the file budget.", "budget"),
        ("Find my file called resume.", "resume"),
        ("find the document tax return and open it.", "tax return"),
        ("open a document named lease agreement", "lease agreement"),
        ("Open the file named Q3 report!", "Q3 report"),
    ],
)
def test_positive_matches_fill_the_file_slot(skill, utterance, name):
    fake = FakeFile()
    skill_id, slots = match(utterance, (skill,), resolvers(fake))
    assert skill_id == "files.find_open"
    assert slots == {"file": f"{RESOLVED_DIR}/{name}"}
    assert fake.calls == [name]  # the captured name, without "and open it" or the punctuation


@pytest.mark.parametrize(
    "utterance",
    [
        "open safari",  # built-in "open <app>"
        "open youtube",
        "open notes",
        "open the downloads folder",  # no file/document word, so not ours
        "search budget",  # built-in search phrasing
        "email the file report to Sarah",  # mail.draft_attachment's phrasing
        "merge these PDFs",  # pdf.merge_selected's phrasing
        "what time is it",
        "volume up",
        "open the file",  # no name given, so the file slot cannot fill
    ],
)
def test_does_not_claim_other_phrasings(skill, utterance):
    with pytest.raises(MatchError):
        match(utterance, (skill,), resolvers())


def test_an_ambiguous_name_is_refused_not_guessed(skill):
    with pytest.raises(MatchError):
        match("open the file budget", (skill,), resolvers(AmbiguousFile()))


def test_slot_validation_rejects_a_too_long_path(skill):
    assert validate_slots(skill, {"file": f"{RESOLVED_DIR}/budget.pdf"}) == []
    assert "SLOT_TOO_LONG" in validate_slots(skill, {"file": "/" + "x" * 1024})


def test_slot_validation_rejects_a_leading_dash(skill):
    assert "DASH_LEADING_SLOT" in validate_slots(skill, {"file": "-rf"})


def test_dry_run_names_the_path(skill):
    path = f"{RESOLVED_DIR}/budget.pdf"
    result = run_skill(skill, {"file": path}, dry_run=True)
    assert result.ok
    assert path in result.output


def test_run_with_fake_backend_opens_the_file(skill):
    path = f"{RESOLVED_DIR}/budget.pdf"
    fake = FakeApplescript("opened budget.pdf")
    result = run_skill(skill, {"file": path}, backends={"applescript": fake})
    assert result.ok and result.postcondition_ok
    ((step, argv),) = fake.calls
    assert step.script == "open_document.applescript"
    assert argv == [path]


class RefusingApplescript(FakeApplescript):
    def run(self, step, slots, timeout=15.0):
        self.calls.append((step, fill_argv(step.argv, slots)))
        return BackendResult(False, "refusing to open Calc.app: not a document")


def test_run_reports_failure_when_the_script_refuses(skill):
    fake = RefusingApplescript("")
    result = run_skill(skill, {"file": f"{RESOLVED_DIR}/Calc.app"}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


def test_unexpected_success_line_fails_the_postcondition(skill):
    fake = FakeApplescript("done")
    result = run_skill(skill, {"file": f"{RESOLVED_DIR}/budget.pdf"}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok
