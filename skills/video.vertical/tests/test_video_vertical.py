"""Tests for video.vertical: "make this video vertical" on the selected video file.

The skill has no slots, so the slot-value checks (SLOT_TOO_LONG, DASH_LEADING_SLOT)
do not apply; the test below asserts it declares none. Nothing here touches Finder,
ffmpeg or the user's files: the applescript backend is always a fake.
"""

import json
import re
from pathlib import Path

import pytest

from friday.policy import ActionClass
from friday.skills.backends.base import BackendResult
from friday.skills.manifest import load_manifest
from friday.skills.matcher import MatchError, match
from friday.skills.runtime import run_skill
from friday.skills.validate import validate_manifest, validate_slots

SKILL_DIR = Path(__file__).parent.parent
SCRIPT = SKILL_DIR / "make_vertical.applescript"
SUCCESS = "started making clip-vertical.mp4 vertical; it will appear next to the original"


@pytest.fixture
def skill():
    return load_manifest(SKILL_DIR / "skill.json")


class FakeApplescript:
    name = "applescript"

    def __init__(self, output, ok=True):
        self.output = output
        self.ok = ok
        self.calls = []

    def run(self, step, slots, timeout=15.0):
        self.calls.append((step, dict(slots)))
        return BackendResult(self.ok, self.output)


def test_skill_loads_and_validates(skill):
    assert skill.id == "video.vertical"
    assert skill.action_class is ActionClass.MUTATE_LOCAL
    assert validate_manifest(skill) == []
    assert dict(skill.slots) == {}
    assert skill.requires.binaries == ("ffmpeg",)
    assert skill.requires.permissions == ("Automation: Finder",)


def test_allowed_dirs_are_the_four_media_folders(skill):
    assert set(skill.allowed_dirs) == {
        "~/Downloads", "~/Documents", "~/Desktop", "~/Movies",
    }


def test_step_is_the_applescript_script_with_selection_argv(skill):
    (step,) = skill.steps
    assert step.backend == "applescript"
    assert step.script == "make_vertical.applescript"
    assert step.argv == ("selection",)


def test_no_slots_means_no_slot_values_to_validate(skill):
    assert validate_slots(skill, {}) == []


def test_examples_all_match(skill):
    lines = (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines()
    assert 12 <= len(lines) <= 20
    for line in lines:
        utterance = json.loads(line)["u"]
        skill_id, slots = match(utterance, (skill,))
        assert skill_id == "video.vertical" and slots == {}


@pytest.mark.parametrize(
    "utterance",
    [
        "make this video vertical",
        "Make the selected video vertical.",
        "turn this video into a reel",
        "convert this video for Instagram!",
        "crop this video to 9 by 16",
        "crop the selected video to 9x16.",
    ],
)
def test_positive_phrasings_match(skill, utterance):
    assert match(utterance, (skill,)) == ("video.vertical", {})


@pytest.mark.parametrize(
    "utterance",
    [
        "make these images web ready",  # images.web_ready
        "merge these PDFs",  # pdf.merge_selected
        "play the video",
        "open the file movie",  # files.find_open
        "open safari",  # built-in
        "search cats",  # built-in
        "volume up",  # built-in
        "what time is it",  # built-in
        "make this video",  # missing the word vertical
        "make this video vertical please",  # extra words fail the anchor
    ],
)
def test_does_not_claim_other_phrasings(skill, utterance):
    with pytest.raises(MatchError):
        match(utterance, (skill,))


def test_dry_run_names_the_output(skill):
    result = run_skill(skill, {}, dry_run=True)
    assert result.ok
    assert "-vertical.mp4" in result.output


def test_run_passes_selection_argv_and_succeeds(skill):
    fake = FakeApplescript(SUCCESS)
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert result.ok and result.postcondition_ok
    (step, _slots), = fake.calls
    assert step.argv == ("selection",)
    assert step.script == "make_vertical.applescript"


def test_run_fails_when_the_script_reports_an_error(skill):
    fake = FakeApplescript("Select one video file in Finder first.", ok=False)
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


def test_run_fails_when_the_success_line_is_missing(skill):
    fake = FakeApplescript("done")
    result = run_skill(skill, {}, backends={"applescript": fake})
    assert not result.ok
    assert not result.postcondition_ok


def test_script_has_the_exception_header_and_fixed_ffmpeg_candidates():
    text = SCRIPT.read_text()
    assert "ALLOWED EXCEPTION TO THE NO-SHELL RULE" in text
    for candidate in (
        "/opt/homebrew/bin/ffmpeg",
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ):
        assert f'"{candidate}"' in text
    assert "ffmpeg is not installed" in text


def test_do_shell_script_quotes_every_path_variable():
    """Text check: each path variable in the shell command is wrapped in `quoted form of`."""
    text = SCRIPT.read_text()
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    assert code.count("do shell script") == 1
    start = text.index("set shellCommand to")
    end = text.index("do shell script shellCommand")
    block = text[start:end]
    for var in ("ffmpegPath", "inPath", "outPath"):
        occurrences = list(re.finditer(rf"\b{var}\b", block))
        assert occurrences, f"{var} is not used in the shell command"
        for m in occurrences:
            assert block[: m.start()].endswith("quoted form of "), (
                f"{var} reaches the shell without `quoted form of`"
            )


def test_shell_command_is_background_and_never_overwrites():
    text = SCRIPT.read_text()
    assert " -nostdin -n -i " in text
    assert "> /dev/null 2>&1 &" in text
