"""When the top route's grammar rejects the utterance, the agent may try the
router's runner-up candidates. Each candidate is still judged by its own slot
grammar and its own confidence, so the fallback never weakens the gate."""

import pytest

import friday.agent as agent_mod
from friday.agent import Agent
from friday.policy import Autonomy
from friday.router import Route


class FixedRouter:
    def __init__(self, route):
        self._route = route

    def route(self, transcript):
        return self._route


@pytest.fixture
def ran(monkeypatch):
    calls = []

    class Result:
        output = "ok"

    def fake_execute(tool, arguments):
        calls.append((tool, arguments))
        return Result()

    monkeypatch.setattr(agent_mod, "execute", fake_execute)
    monkeypatch.setattr("friday.registry.known_apps", lambda: ("Safari", "Notes"))
    return calls


def make(tmp_path, route, confirm=lambda d, v: True, autonomy=Autonomy.CAUTIOUS):
    return Agent(router=FixedRouter(route), confirm=confirm, autonomy=autonomy,
                 log_path=tmp_path / "audit.jsonl")


def test_runner_up_runs_when_the_top_route_grammar_rejects(tmp_path, ran):
    route = Route("navigate_tab", 0.83, alternatives=(Route("use_app", 0.6),))
    outcome = make(tmp_path, route).handle("open safari")
    assert outcome.status == "executed"
    assert ran == [("use_app", {"app": "Safari"})]


def test_runner_up_is_judged_at_its_own_confidence(tmp_path, ran):
    # Strict confirms everything; the description shown is the runner-up's.
    seen = []
    route = Route("navigate_tab", 0.83, alternatives=(Route("use_app", 0.6),))
    agent = make(tmp_path, route, confirm=lambda d, v: (seen.append(d), False)[1],
                 autonomy=Autonomy.STRICT)
    outcome = agent.handle("open safari")
    assert outcome.status == "denied"
    assert "use_app" in seen[0]
    assert ran == []


def test_refuses_with_the_top_route_error_when_no_candidate_fits(tmp_path, ran):
    route = Route("navigate_tab", 0.83, alternatives=(Route("speak_time", 0.1),))
    outcome = make(tmp_path, route).handle("blorp the flurb")
    assert outcome.status == "refused"
    assert "tab" in outcome.message
    assert ran == []


def test_audit_records_which_candidate_ran(tmp_path, ran):
    import json

    route = Route("navigate_tab", 0.83, alternatives=(Route("use_app", 0.6),))
    make(tmp_path, route).handle("open safari")
    record = json.loads((tmp_path / "audit.jsonl").read_text().splitlines()[-1])
    assert record["tool"] == "use_app"
    assert record["act_probability"] == 0.6
    assert record["fallback_from"] == "navigate_tab"


@pytest.mark.parametrize("tool", ["speak_time", "lock_screen", "take_screenshot", "close_tab"])
def test_argument_free_tools_never_win_by_fallback(tmp_path, ran, tool):
    route = Route("navigate_tab", 0.83, alternatives=(Route(tool, 0.8),))
    outcome = make(tmp_path, route).handle("close the tab and lock everything")
    assert outcome.status == "refused"
    assert ran == []
