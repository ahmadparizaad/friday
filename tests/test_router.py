"""Router tests — ARCHITECTURE.md §5.2.

These run headless against recorded laya output shapes. The live suite
(test_router_live.py) is what proves the shapes are real.
"""

import pytest

from friday.registry import REGISTRY
from friday.router import REFUSE_BELOW, Route, RouteError, parse_prediction


def a_prediction(**scores):
    """The real shape laya.predict returns for per-tool nouls —
    verified on-device, recorded in docs/ROUTING.md."""
    if not scores:
        scores = {"use_app": 0.94, "speak_time": 0.01}
    return {
        "answers": {
            name: {
                "type": "noul",
                "noul": value,
                "confidence": 0.8,
                "action": {"act_probability": 1.0},
            }
            for name, value in scores.items()
        },
        "usage": {"input_tokens": 92, "output_tokens": 0},
    }


def test_picks_the_highest_scoring_tool():
    route = parse_prediction(a_prediction(use_app=0.91, speak_time=0.04))
    assert isinstance(route, Route)
    assert route.tool == "use_app"
    assert route.confidence == 0.91


def test_picks_speak_time_when_it_scores_higher():
    route = parse_prediction(a_prediction(use_app=0.03, speak_time=0.88))
    assert route.tool == "speak_time"


def test_refuses_when_no_tool_clears_the_floor():
    """An out-of-scope request should score low on every tool. Returning
    the least-bad one would be a guess (§5.3)."""
    low = REFUSE_BELOW / 2
    with pytest.raises(RouteError, match="no tool matched"):
        parse_prediction(a_prediction(use_app=low, speak_time=low))


def test_accepts_a_score_exactly_at_the_floor():
    route = parse_prediction(
        a_prediction(use_app=REFUSE_BELOW, speak_time=0.0)
    )
    assert route.tool == "use_app"


def test_rejects_an_answer_for_an_unknown_tool():
    """The structural R1 guarantee: a name outside the registry is not
    unlikely, it is rejected."""
    with pytest.raises(RouteError, match="unknown tool"):
        parse_prediction(a_prediction(delete_everything=0.99))


def test_rejects_missing_answers():
    with pytest.raises(RouteError):
        parse_prediction({"usage": {}})


def test_rejects_empty_answers():
    with pytest.raises(RouteError):
        parse_prediction({"answers": {}})


def test_rejects_an_answer_that_is_not_a_noul():
    with pytest.raises(RouteError, match="not a noul"):
        parse_prediction({"answers": {"use_app": {"type": "choice"}}})


@pytest.mark.parametrize("value", [-0.1, 1.5, "high", None, True])
def test_rejects_malformed_scores(value):
    with pytest.raises(RouteError):
        parse_prediction({"answers": {"use_app": {"noul": value}}})


def test_rejects_answers_shaped_as_a_bare_string():
    """Guards against a future laya version flattening the shape without
    us noticing — fail loudly rather than mis-parse."""
    with pytest.raises(RouteError):
        parse_prediction({"answers": {"use_app": "yes"}})


def test_every_registry_tool_is_routable():
    for name in REGISTRY:
        route = parse_prediction(a_prediction(**{name: 0.99}))
        assert route.tool == name


def test_questions_cover_every_tool():
    from friday.router import build_questions

    questions = build_questions()
    assert set(questions) == set(REGISTRY)
    for name, question in questions.items():
        assert question["type"] == "noul"
        assert question["instructions"] == REGISTRY[name].description


# --- runner-up candidates ----------------------------------------------------------


def test_route_carries_the_next_two_candidates_in_score_order():
    route = parse_prediction(
        a_prediction(navigate_tab=0.83, use_app=0.6, open_url=0.3, speak_time=0.2)
    )
    assert route == Route("navigate_tab", 0.83, alternatives=(
        Route("use_app", 0.6), Route("open_url", 0.3),
    ))


def test_candidates_below_the_refuse_floor_are_not_offered():
    route = parse_prediction(a_prediction(use_app=0.9, speak_time=REFUSE_BELOW / 2))
    assert route.alternatives == ()
