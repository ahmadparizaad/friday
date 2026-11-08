"""Tests for web.search_here: "search X" on the site the current tab is on."""

import json
import sys
from pathlib import Path

import pytest

from friday.browser import reset_backend, set_backend
from friday.policy import ActionClass
from friday.skills.manifest import load_manifest
from friday.skills.matcher import MatchError, match
from friday.skills.runtime import run_skill
from friday.skills.validate import validate_manifest, validate_slots

SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR.parent.parent / "tests"))
from test_browser_backend import FakeBackendForBrowser  # noqa: E402


@pytest.fixture
def skill():
    return load_manifest(SKILL_DIR / "skill.json")


def test_skill_loads_and_validates(skill):
    assert skill.id == "web.search_here"
    assert skill.action_class is ActionClass.NAVIGATE
    assert set(skill.slots) == {"query"}
    assert validate_manifest(skill) == []


def test_uses_no_mutating_verb(skill):
    assert {s.argv[0] for s in skill.steps} == {"search_here"}


def test_slot_validation(skill):
    assert validate_slots(skill, {"query": "artificial intelligence"}) == []
    assert "SLOT_TOO_LONG" in validate_slots(skill, {"query": "x" * 201})


def test_examples_all_match(skill):
    for line in (SKILL_DIR / "examples.jsonl").read_text().strip().splitlines():
        utterance = json.loads(line)["u"]
        match(utterance, (skill,))  # raises MatchError if not


@pytest.mark.parametrize(
    "utterance, query",
    [
        ("Search artificial intelligence", "artificial intelligence"),
        ("search for artificial intelligence", "artificial intelligence"),
        ("search how to tie a tie.", "how to tie a tie."),
    ],
)
def test_bare_search_fills_the_query(skill, utterance, query):
    skill_id, slots = match(utterance, (skill,))
    assert skill_id == "web.search_here" and slots == {"query": query}


@pytest.mark.parametrize(
    "utterance",
    [
        "search github for laya mlx",  # browser_search's phrasing, not ours
        "search youtube for cats",
        "search stack overflow for python",
        "open the invoice",
        "please search artificial intelligence",  # must start with the verb
    ],
)
def test_does_not_claim_other_phrasings(skill, utterance):
    with pytest.raises(MatchError):
        match(utterance, (skill,))


def test_dry_run_reports_the_step(skill):
    result = run_skill(skill, {"query": "artificial intelligence"}, dry_run=True)
    assert result.ok
    assert "artificial intelligence" in result.output


def _run_on(skill, url):
    fake = FakeBackendForBrowser()
    fake.tabs = [url]
    set_backend(fake)
    try:
        return run_skill(skill, {"query": "artificial intelligence"}), fake
    finally:
        reset_backend()


def test_on_youtube_it_searches_youtube(skill):
    result, fake = _run_on(skill, "https://www.youtube.com/watch?v=abc")
    assert result.ok and result.postcondition_ok
    assert fake.navigated == [
        "https://www.youtube.com/results?search_query=artificial%20intelligence"
    ]


def test_on_an_unknown_site_it_refuses_and_does_not_navigate(skill):
    result, fake = _run_on(skill, "https://example.com/")
    assert not result.ok
    assert fake.navigated == []
