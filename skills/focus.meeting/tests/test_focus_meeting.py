"""Tests for focus.meeting: "I'm going into a meeting" / "meeting mode".

No test touches the real apps: the applescript backend is always a fake,
and the resolver bundle is replaced with objects that fail if called.
"""

import json
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from friday.policy import ActionClass
from friday.skills.backends.base import BackendResult, fill_argv
from friday.skills.manifest import Slot, load_manifest
from friday.skills.matcher import MatchError, Resolvers, match
from friday.skills.runtime import run_skill
from friday.skills.validate import (
    DASH_LEADING_SLOT,
    SLOT_TOO_LONG,
    validate_manifest,
    validate_slots,
)

SKILL_DIR = Path(__file__).parent.parent
SCRIPT_PATH = SKILL_DIR / "meeting_mode.applescript"
SCRIPT = SCRIPT_PATH.read_text(encoding="utf-8")
SUCCESS_LINE = "meeting mode on: muted, quit Slack, opened zoom.us link"


class FakeApplescript:
    name = "applescript"

    def __init__(self, output: str, ok: bool = True):
        self.output = output
        self.ok = ok
        self.calls: list[tuple] = []

    def run(self, step, slots, timeout=15.0):
        self.calls.append((step, dict(slots)))
        return BackendResult(self.ok, self.output)


class ResolverMustNotRun:
    def resolve(self, text):
        raise AssertionError("focus.meeting has no file/app/contact slots")


def _resolvers():
    return Resolvers(
        file=ResolverMustNotRun(),
        app=ResolverMustNotRun(),
        contact=ResolverMustNotRun(),
    )


@pytest.fixture
def skill():
    return load_manifest(SKILL_DIR / "skill.json")


def _property_list(name: str) -> list[str]:
    m = re.search(rf"^property {name} : \{{(.*)\}}$", SCRIPT, re.MULTILINE)
    assert m, f"property {name} not found in script"
    return re.findall(r'"([^"]*)"', m.group(1))


def test_skill_loads_and_validates(skill):
    assert skill.id == "focus.meeting"
    assert skill.version == "0.2.0"
    assert validate_manifest(skill) == []


def test_action_class_is_mutate_local_and_has_no_slots(skill):
    assert skill.action_class is ActionClass.MUTATE_LOCAL
    assert len(skill.slots) == 0
    assert skill.undo == "unmute the sound and reopen the chat apps"
    assert skill.postcondition.type == "output_matches"
    assert skill.postcondition.value == "^meeting mode on: "


def test_single_applescript_step_with_fixed_argv(skill):
    assert len(skill.steps) == 1
    step = skill.steps[0]
    assert step.backend == "applescript"
    assert step.script == "meeting_mode.applescript"
    assert step.argv == ("start",)


def test_requires_calendar_and_automation(skill):
    assert "Calendar" in skill.requires.apps
    assert "Automation: Calendar" in skill.requires.permissions
    assert "Automation: System Events" in skill.requires.permissions


def test_examples_all_match(skill):
    lines = (SKILL_DIR / "examples.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 12
    for line in lines:
        utterance = json.loads(line)["u"]
        skill_id, slots = match(utterance, (skill,), _resolvers())
        assert skill_id == "focus.meeting", utterance
        assert slots == {}, utterance


@pytest.mark.parametrize(
    "utterance",
    [
        "I'm going into a meeting",
        "I'm going into a meeting.",
        "I’m in a meeting",
        "I am in a meeting!",
        "meeting mode",
        "Meeting mode.",
        "start meeting mode",
        "join my meeting",
    ],
)
def test_positive_phrasings_match(skill, utterance):
    skill_id, slots = match(utterance, (skill,), _resolvers())
    assert skill_id == "focus.meeting"
    assert slots == {}


@pytest.mark.parametrize(
    "utterance",
    [
        "what's on my calendar today",  # day.agenda
        "remind me about the meeting at 5",  # reminders.add
        "remind me to call the bank at 5 pm",  # reminders.add
        "open zoom",  # built-in open <app>
        "open safari",  # built-in open <app>
        "volume up",  # built-in
        "mute",  # built-in volume control, not meeting mode
        "search cats",  # built-in search
        "what time is it",  # built-in
        "please start meeting mode",  # must start with the phrase
        "I'm going to lunch",
    ],
)
def test_negative_phrasings_do_not_match(skill, utterance):
    with pytest.raises(MatchError):
        match(utterance, (skill,), _resolvers())


def test_dry_run_describes_the_step(skill):
    result = run_skill(skill, {}, dry_run=True)
    assert result.ok
    assert "mute the sound" in result.output
    assert "Slack" in result.output
    assert "Calendar" in result.output


def test_slot_validation_codes_still_apply(skill):
    # The skill has no slots, so probe the validator with a temporary one.
    probe = replace(
        skill,
        slots=MappingProxyType(
            {"note": Slot(name="note", type="text", provenance="utterance", max_len=10)}
        ),
    )
    assert SLOT_TOO_LONG in validate_slots(probe, {"note": "x" * 11})
    assert DASH_LEADING_SLOT in validate_slots(probe, {"note": "-rf"})
    assert validate_slots(probe, {"note": "short"}) == []


def test_run_with_fake_backend_succeeds(skill):
    fake = FakeApplescript(SUCCESS_LINE)
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert result.ok
    assert result.postcondition_ok
    assert len(fake.calls) == 1
    step, _ = fake.calls[0]
    assert step.script == "meeting_mode.applescript"
    assert fill_argv(step.argv, {}) == ["start"]


def test_run_with_fake_failure_is_not_ok(skill):
    fake = FakeApplescript("osascript: execution error", ok=False)
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


def test_unexpected_output_fails_the_postcondition(skill):
    fake = FakeApplescript("something else entirely")
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


# --- script text checks (the script itself is never run here) -------------


def test_meeting_host_allow_list_is_exact():
    assert sorted(_property_list("meetingHosts")) == sorted(
        ["meet.google.com", "teams.microsoft.com", "zoom.us"]
    )
    assert _property_list("meetingHostSuffixes") == [".zoom.us"]


def test_chat_app_list_is_fixed():
    assert _property_list("chatApps") == [
        "Slack",
        "WhatsApp",
        "Discord",
        "Messages",
        "Telegram",
    ]


def test_script_only_opens_a_link_in_one_place_and_never_shells_out():
    code = [ln for ln in SCRIPT.splitlines() if not ln.lstrip().startswith("--")]
    assert sum("open location" in ln for ln in code) == 1
    assert "do shell script" not in SCRIPT.lower()
    assert "kill" not in "\n".join(code).lower()


@pytest.mark.skipif(shutil.which("osacompile") is None, reason="needs macOS osacompile")
def test_script_compiles(tmp_path):
    out = tmp_path / "meeting_mode.scpt"
    proc = subprocess.run(
        ["osacompile", "-o", str(out), str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr


def test_focus_shortcut_is_optional_and_never_fails_the_skill():
    script = (SKILL_DIR / "meeting_mode.applescript").read_text()
    assert 'property focusShortcut : "Friday Focus On"' in script
    assert 'if not (exists shortcut focusShortcut) then return "no Focus shortcut"' in script
