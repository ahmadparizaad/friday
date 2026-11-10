"""Tests for mail.draft_attachment: "email the file X to Y" opens a Mail draft.

Every resolver is a fake. Nothing here calls Spotlight, Contacts, Mail or
osascript, and nothing sends mail.
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path

import pytest

from friday.policy import ActionClass
from friday.skills.backends.base import BackendResult, fill_argv
from friday.skills.manifest import Skill, load_manifest
from friday.skills.matcher import MatchError, Resolvers, match
from friday.skills.resolvers import ResolverError
from friday.skills.runtime import run_skill
from friday.skills.validate import (
    DASH_LEADING_SLOT,
    SLOT_TOO_LONG,
    validate_manifest,
    validate_slots,
)

SKILL_DIR = Path(__file__).parent.parent
SCRIPT = SKILL_DIR / "draft_mail.applescript"
DOCS = "/Users/test/Documents"
SARAH = "sarah@example.com"


class FakeFile:
    """Resolves any name to a path under Documents, or only the names given."""

    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known

    def resolve(self, text: str) -> str:
        if self.known is not None and text not in self.known:
            raise ResolverError(f"no file named {text!r}")
        return f"{DOCS}/{text}.pdf"


class FakeContact:
    """Resolves names from a table; `any_name` accepts every name."""

    def __init__(
        self,
        table: dict[str, str] | None = None,
        any_name: bool = False,
        error: str | None = None,
    ) -> None:
        self.table = table or {}
        self.any_name = any_name
        self.error = error

    def resolve(self, text: str) -> str:
        if self.error is not None:
            raise ResolverError(self.error)
        if self.any_name:
            return re.sub(r"\W+", ".", text.strip().lower()).strip(".") + "@example.com"
        if text not in self.table:
            raise ResolverError(f"no contact named {text!r}")
        return self.table[text]


class FakeApp:
    def resolve(self, text: str) -> str:
        raise ResolverError("apps are not used by this skill")


class FakeMail:
    """Stands in for the applescript backend; records what it was asked to run."""

    name = "applescript"

    def __init__(self, result: BackendResult | None = None) -> None:
        self.result = result or BackendResult(
            True, "draft ready for sarah@example.com with quarterly report.pdf"
        )
        self.argv: list[str] | None = None

    def run(self, step, slots: Mapping[str, str], timeout: float = 15.0) -> BackendResult:
        self.argv = fill_argv(step.argv, slots)
        return self.result


@pytest.fixture
def skill() -> Skill:
    return load_manifest(SKILL_DIR / "skill.json")


@pytest.fixture
def resolvers() -> Resolvers:
    return Resolvers(
        file=FakeFile(),
        app=FakeApp(),
        contact=FakeContact(
            {
                "Sarah": SARAH,
                "John Smith": "john@example.com",
                "Priya": "priya@example.com",
            }
        ),
    )


# --- manifest ----------------------------------------------------------------


def test_skill_loads_and_validates(skill: Skill) -> None:
    assert skill.id == "mail.draft_attachment"
    assert skill.action_class is ActionClass.MUTATE_LOCAL
    assert set(skill.slots) == {"file", "to"}
    assert skill.slots["file"].type == "file"
    assert skill.slots["file"].provenance == "resolver"
    assert skill.slots["to"].type == "contact"
    assert skill.slots["to"].provenance == "resolver"
    assert validate_manifest(skill) == []


def test_one_applescript_step_with_the_draft_script(skill: Skill) -> None:
    assert [s.backend for s in skill.steps] == ["applescript"]
    step = skill.steps[0]
    assert step.script == "draft_mail.applescript"
    assert step.argv == ("{to}", "{file}")


def test_postcondition_and_undo(skill: Skill) -> None:
    assert skill.postcondition.type == "output_matches"
    assert skill.postcondition.value == "^draft ready for .+ with .+"
    assert skill.undo == "close the draft without saving"
    assert set(skill.requires.apps) == {"Mail"}
    assert "Automation: Mail" in skill.requires.permissions
    assert "Automation: Contacts" in skill.requires.permissions


def test_script_never_sends_mail() -> None:
    code_lines = [
        line
        for line in SCRIPT.read_text().splitlines()
        if not line.lstrip().startswith("--")
    ]
    assert not [line for line in code_lines if re.match(r"^\s*send\b", line)]
    assert "send" not in " ".join(code_lines).lower()


# --- examples and matching -----------------------------------------------------


def test_examples_all_match(skill: Skill) -> None:
    # every spoken example must match; contacts and files are faked
    fake = Resolvers(file=FakeFile(), app=FakeApp(), contact=FakeContact(any_name=True))
    for line in (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines():
        utterance = json.loads(line)["u"]
        skill_id, _ = match(utterance, (skill,), fake)
        assert skill_id == "mail.draft_attachment", utterance


@pytest.mark.parametrize(
    "utterance, file_name, address",
    [
        ("email the file quarterly report to Sarah", "quarterly report", SARAH),
        ("send the file budget to John Smith", "budget", "john@example.com"),
        ("mail the document invoice to Priya", "invoice", "priya@example.com"),
        # speech-to-text trailing punctuation must not reach the contact lookup
        ("Mail the document invoice to Priya.", "invoice", "priya@example.com"),
    ],
)
def test_positive_matches_fill_the_slots(
    skill: Skill, resolvers: Resolvers, utterance: str, file_name: str, address: str
) -> None:
    skill_id, slots = match(utterance, (skill,), resolvers)
    assert skill_id == "mail.draft_attachment"
    assert slots == {"file": f"{DOCS}/{file_name}.pdf", "to": address}


@pytest.mark.parametrize(
    "utterance",
    [
        "open the file budget",  # built-in files.find_open phrasing
        "search email",  # built-in search
        "what's on my calendar today",  # day.agenda
        "remind me to email Sarah",  # reminders.add
        "find my file called resume",  # files.find_open
        "open safari",  # built-in app open
        "search cats",  # built-in search
        "volume up",  # built-in volume
        "what time is it",  # built-in time
        "email the report to Sarah",  # no "file" or "document" word
        "email the file budget",  # no recipient
    ],
)
def test_negative_phrases_do_not_match(
    skill: Skill, resolvers: Resolvers, utterance: str
) -> None:
    with pytest.raises(MatchError):
        match(utterance, (skill,), resolvers)


def test_unknown_contact_is_a_match_error(skill: Skill) -> None:
    res = Resolvers(file=FakeFile(), app=FakeApp(), contact=FakeContact({}))
    with pytest.raises(MatchError):
        match("email the file budget to Nobody", (skill,), res)


def test_ambiguous_contact_is_a_match_error(skill: Skill) -> None:
    res = Resolvers(
        file=FakeFile(),
        app=FakeApp(),
        contact=FakeContact(error="ambiguous: 'Sam' matches Sam Lee, Sam Ray"),
    )
    with pytest.raises(MatchError):
        match("send the file budget to Sam", (skill,), res)


def test_unknown_file_is_a_match_error(skill: Skill) -> None:
    res = Resolvers(
        file=FakeFile(known={"budget"}),
        app=FakeApp(),
        contact=FakeContact({"Sarah": SARAH}),
    )
    with pytest.raises(MatchError):
        match("email the file invoice to Sarah", (skill,), res)


# --- slot validation -----------------------------------------------------------


def test_too_long_contact_is_rejected(skill: Skill) -> None:
    assert SLOT_TOO_LONG in validate_slots(skill, {"to": "a" * 255})


def test_too_long_file_is_rejected(skill: Skill) -> None:
    assert SLOT_TOO_LONG in validate_slots(skill, {"file": "/" + "a" * 1024})


def test_leading_dash_is_rejected(skill: Skill) -> None:
    assert DASH_LEADING_SLOT in validate_slots(skill, {"to": "-x@example.com"})


def test_valid_slots_pass(skill: Skill) -> None:
    assert validate_slots(skill, {"file": f"{DOCS}/budget.pdf", "to": SARAH}) == []


# --- running ---------------------------------------------------------------------


SLOTS = {"file": f"{DOCS}/quarterly report.pdf", "to": SARAH}


def test_dry_run_names_the_recipient_and_file(skill: Skill) -> None:
    result = run_skill(skill, SLOTS, dry_run=True)
    assert result.ok
    assert SARAH in result.output
    assert f"{DOCS}/quarterly report.pdf" in result.output
    assert "not sent" in result.output


def test_run_with_fake_backend_succeeds_and_passes_argv(skill: Skill) -> None:
    fake = FakeMail()
    result = run_skill(skill, SLOTS, backends={"applescript": fake})
    assert result.ok and result.postcondition_ok
    assert fake.argv == [SARAH, f"{DOCS}/quarterly report.pdf"]


def test_failure_line_from_script_fails_the_skill(skill: Skill) -> None:
    fake = FakeMail(BackendResult(False, "the file does not exist: /x.pdf"))
    result = run_skill(skill, SLOTS, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


def test_unexpected_output_fails_the_postcondition(skill: Skill) -> None:
    fake = FakeMail(BackendResult(True, "sent it"))
    result = run_skill(skill, SLOTS, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok
