"""Policy gate tests — ARCHITECTURE.md §5.4.

This is the module whose bugs are dangerous rather than annoying, so the
tests are exhaustive over (class x slider x confidence) rather than
representative.
"""

import itertools

import pytest

from friday.policy import (
    _ALLOW_THRESHOLD,
    _REFUSE_THRESHOLD,
    ActionClass,
    Autonomy,
    Decision,
    Verdict,
    evaluate,
    unreachable_refuse_thresholds,
)

ALL_CLASSES = list(ActionClass)
ALL_AUTONOMY = list(Autonomy)

# Expressed relative to the thresholds, not as literals: the router's
# confidence scale moved once already and every literal broke (docs/ROUTING.md).
ALLOW = _ALLOW_THRESHOLD[Autonomy.CAUTIOUS]
REFUSE = _REFUSE_THRESHOLD[Autonomy.CAUTIOUS]
LOOSE_ALLOW = _ALLOW_THRESHOLD[Autonomy.PERMISSIVE]
UNCERTAIN = (REFUSE + ALLOW) / 2  # between refuse and allow for CAUTIOUS
UNCONFIDENT = REFUSE / 2  # below the CAUTIOUS refuse bar
MID = (LOOSE_ALLOW + ALLOW) / 2  # allowed by PERMISSIVE, not by CAUTIOUS

# Spread of confidence including every threshold boundary exactly.
CONFIDENCES = sorted(
    {0.0, 0.01, 0.1, 0.3, 0.5, 0.7, 0.85, 0.99, 1.0}
    | set(_ALLOW_THRESHOLD.values())
    | set(_REFUSE_THRESHOLD.values())
)


def decide(cls, conf, autonomy=Autonomy.CAUTIOUS):
    return evaluate(cls, conf, autonomy)


# --- the floors: these hold regardless of confidence or slider ------------


@pytest.mark.parametrize("conf", CONFIDENCES)
@pytest.mark.parametrize("autonomy", ALL_AUTONOMY)
def test_blocked_always_refuses(conf, autonomy):
    """`blocked` is not on the slider and is not loosenable. Ever."""
    assert decide(ActionClass.BLOCKED, conf, autonomy) == Verdict.REFUSE


@pytest.mark.parametrize("conf", CONFIDENCES)
@pytest.mark.parametrize("autonomy", ALL_AUTONOMY)
@pytest.mark.parametrize(
    "cls", [ActionClass.MUTATE_REMOTE, ActionClass.DESTRUCTIVE]
)
def test_high_risk_never_auto_allows(cls, conf, autonomy):
    """High model confidence about a destructive action is not the same
    as the action being safe (§5.4, R2)."""
    assert decide(cls, conf, autonomy) != Verdict.ALLOW


@pytest.mark.parametrize("conf", CONFIDENCES)
@pytest.mark.parametrize("autonomy", ALL_AUTONOMY)
def test_mutate_local_never_auto_allows(conf, autonomy):
    assert decide(ActionClass.MUTATE_LOCAL, conf, autonomy) != Verdict.ALLOW


@pytest.mark.parametrize(
    "cls", [ActionClass.MUTATE_REMOTE, ActionClass.DESTRUCTIVE]
)
def test_high_risk_demands_verbatim_readback(cls):
    d = decide(cls, 0.99, Autonomy.PERMISSIVE)
    assert d == Verdict.CONFIRM
    assert d.requires_verbatim_readback


def test_mutate_local_confirms_without_requiring_readback():
    d = decide(ActionClass.MUTATE_LOCAL, 0.99, Autonomy.PERMISSIVE)
    assert d == Verdict.CONFIRM
    assert not d.requires_verbatim_readback


# --- confidence gating applies only to the low-risk classes ---------------


@pytest.mark.parametrize("cls", [ActionClass.READ, ActionClass.NAVIGATE])
def test_low_risk_allows_when_confident(cls):
    assert decide(cls, 0.99, Autonomy.CAUTIOUS) == Verdict.ALLOW


@pytest.mark.parametrize("cls", [ActionClass.READ, ActionClass.NAVIGATE])
def test_low_risk_confirms_when_uncertain(cls):
    assert decide(cls, UNCERTAIN, Autonomy.CAUTIOUS) == Verdict.CONFIRM


@pytest.mark.parametrize("cls", [ActionClass.READ, ActionClass.NAVIGATE])
def test_low_risk_refuses_when_unconfident(cls):
    assert decide(cls, UNCONFIDENT, Autonomy.CAUTIOUS) == Verdict.REFUSE


@pytest.mark.parametrize("autonomy", [Autonomy.CAUTIOUS, Autonomy.PERMISSIVE])
def test_allow_threshold_is_inclusive(autonomy):
    t = _ALLOW_THRESHOLD[autonomy]
    assert decide(ActionClass.READ, t, autonomy) == Verdict.ALLOW


def test_slider_can_loosen_low_risk_only():
    """PERMISSIVE lowers the bar for navigate, but cannot touch a floor."""
    assert decide(ActionClass.NAVIGATE, MID, Autonomy.CAUTIOUS) == Verdict.CONFIRM
    assert decide(ActionClass.NAVIGATE, MID, Autonomy.PERMISSIVE) == Verdict.ALLOW
    # ...while the floor classes are unmoved by the same change.
    assert decide(ActionClass.DESTRUCTIVE, MID, Autonomy.PERMISSIVE) == Verdict.CONFIRM


def test_strict_never_auto_allows_anything():
    """STRICT is the 'confirm everything' setting."""
    for cls, conf in itertools.product(ALL_CLASSES, CONFIDENCES):
        assert decide(cls, conf, Autonomy.STRICT) != Verdict.ALLOW


def test_slider_is_monotonic():
    """Loosening the slider must never make a verdict stricter."""
    order = {Verdict.REFUSE: 0, Verdict.CONFIRM: 1, Verdict.ALLOW: 2}
    for cls, conf in itertools.product(ALL_CLASSES, CONFIDENCES):
        seq = [order[decide(cls, conf, a).verdict] for a in ALL_AUTONOMY]
        assert seq == sorted(seq), (cls, conf, seq)


# --- input validation -----------------------------------------------------


@pytest.mark.parametrize("conf", [-0.01, 1.01, float("nan")])
def test_rejects_out_of_range_confidence(conf):
    """A malformed probability must fail loudly, not silently clamp into
    a permissive verdict."""
    with pytest.raises(ValueError):
        decide(ActionClass.NAVIGATE, conf)


def test_decision_is_immutable():
    d = decide(ActionClass.READ, 0.99)
    with pytest.raises(Exception):
        d.verdict = Verdict.ALLOW


def test_decision_is_unhashable():
    """__eq__ compares against Verdict, so a field-derived hash would be
    inconsistent with it: two Decisions with equal fields would hash the
    same but compare unequal, silently breaking set/dict semantics.

    `@dataclass(frozen=True)` regenerates __hash__ after the class body
    runs, so a bare `__hash__ = None` in the body does NOT survive —
    eq=False is what actually holds.
    """
    d = decide(ActionClass.READ, 0.99)
    with pytest.raises(TypeError):
        hash(d)
    with pytest.raises(TypeError):
        {d: 1}


def test_decision_carries_its_reason():
    """The audit log records why, not just what (§5.5, R5)."""
    d = decide(ActionClass.DESTRUCTIVE, 0.99, Autonomy.PERMISSIVE)
    assert isinstance(d, Decision)
    assert d.reason


# --- router/policy floor invariant ----------------------------------------


def test_router_floor_below_every_refuse_threshold_is_fine():
    assert unreachable_refuse_thresholds(min(_REFUSE_THRESHOLD.values())) == {}


def test_router_floor_above_a_refuse_threshold_is_reported():
    """A router floor above a policy threshold silently steals that decision."""
    dead = unreachable_refuse_thresholds(max(_REFUSE_THRESHOLD.values()) + 0.01)
    assert set(dead) == {a.value for a in Autonomy}
