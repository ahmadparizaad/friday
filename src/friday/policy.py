"""The policy gate — ARCHITECTURE.md §5.4.

Pure: no I/O, no clock, no globals. Given an action class, the router's
confidence, and the user's autonomy setting, return a verdict.

The load-bearing property is the *floor*: for the dangerous classes the
verdict does not depend on confidence at all. High model confidence about
deleting files is not the same as deleting files being safe (R2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class ActionClass(Enum):
    """Declared per tool in the registry."""

    READ = "read"
    NAVIGATE = "navigate"
    MUTATE_LOCAL = "mutate_local"
    MUTATE_REMOTE = "mutate_remote"
    DESTRUCTIVE = "destructive"
    BLOCKED = "blocked"


class Autonomy(Enum):
    """The user-visible slider (R7), ordered loosest-last.

    `Verdict` monotonicity is tested against this order, so new members
    must be inserted in increasing permissiveness.
    """

    STRICT = "strict"
    CAUTIOUS = "cautious"
    PERMISSIVE = "permissive"


class Verdict(Enum):
    REFUSE = "refuse"
    CONFIRM = "confirm"
    ALLOW = "allow"


# Classes that may never auto-execute, whatever the confidence or slider.
_NEVER_ALLOW = {
    ActionClass.MUTATE_LOCAL,
    ActionClass.MUTATE_REMOTE,
    ActionClass.DESTRUCTIVE,
}

# Classes whose confirmation must read arguments back verbatim (§9.2).
_VERBATIM = {ActionClass.MUTATE_REMOTE, ActionClass.DESTRUCTIVE}

# These are calibrated to the router's confidence scale, which is P(true)
# from a per-tool noul — NOT a choice probability. Correct routes measure
# min 0.046, median 0.421, max 0.854 on the dev corpus (docs/ROUTING.md).
# Thresholds set for a choice scale refused 22/36 correct routes, so the
# two must be re-measured together if the router's scoring changes.

# Confidence needed to auto-allow a low-risk action, per slider position.
# STRICT is absent: it never auto-allows.
#
# This threshold exists so correct routes do not all require
# confirmation. It is not a boundary on out-of-scope requests: junk
# measures up to 0.540 against the 0.45 CAUTIOUS bar, so some of it
# clears the bar (docs/ROUTING.md).
#
# Raising it would catch *that* case — 0.60 would push 0.540 to CONFIRM —
# but it buys confirmation prompts on correct routes too (median 0.421),
# and the next junk utterance may score higher still. So treat this as a
# usability/friction dial, not the thing that keeps the system safe. What
# actually bounds the damage is the action class, the slot allow-list,
# and what the registry contains at all.
_ALLOW_THRESHOLD = {
    Autonomy.CAUTIOUS: 0.45,  # ~ the median correct route
    Autonomy.PERMISSIVE: 0.15,
}

# Below this, we don't believe the route at all and say so. The router
# already refuses below its own floor (REFUSE_BELOW), so this is a second,
# policy-side check rather than the only one.
_REFUSE_THRESHOLD = {
    Autonomy.STRICT: 0.04,
    Autonomy.CAUTIOUS: 0.04,
    Autonomy.PERMISSIVE: 0.02,
}

# Slider positions that never auto-allow, so carry no allow threshold.
_ALWAYS_CONFIRMS = frozenset({Autonomy.STRICT})

# Adding an Autonomy member without deciding its thresholds would
# otherwise fall through to "confirms everything" silently. That is the
# safe direction, but it should be an explicit choice, not an omission.
_overlap = set(_ALLOW_THRESHOLD) & _ALWAYS_CONFIRMS
if _overlap:
    raise RuntimeError(
        f"Autonomy members both in _ALLOW_THRESHOLD and _ALWAYS_CONFIRMS: "
        f"{sorted(a.value for a in _overlap)}. evaluate() derives "
        "'always confirms' from ABSENCE in _ALLOW_THRESHOLD, so an entry "
        "here would silently win and the member would auto-allow."
    )

_unclassified = set(Autonomy) - set(_ALLOW_THRESHOLD) - _ALWAYS_CONFIRMS
if _unclassified:
    raise RuntimeError(
        f"Autonomy members with no allow policy: {sorted(a.value for a in _unclassified)}. "
        "Add them to _ALLOW_THRESHOLD or _ALWAYS_CONFIRMS."
    )
_missing_refuse = set(Autonomy) - set(_REFUSE_THRESHOLD)
if _missing_refuse:
    raise RuntimeError(
        f"Autonomy members with no refuse threshold: "
        f"{sorted(a.value for a in _missing_refuse)}."
    )


def unreachable_refuse_thresholds(router_floor: float) -> dict[str, float]:
    """Policy refuse thresholds that a router floor would make dead.

    The router refuses below its own floor before the gate is ever
    reached, so a floor above one of these thresholds means the router
    silently owns a decision policy is supposed to make.

    Takes the floor as an argument rather than importing the router:
    `policy -> router -> registry -> policy` is a real cycle (verified,
    it raises ImportError). The router calls this at import instead, so
    the invariant is enforced in production rather than only in tests.
    """
    return {
        autonomy.value: threshold
        for autonomy, threshold in _REFUSE_THRESHOLD.items()
        if router_floor > threshold
    }


# eq=False keeps the dataclass machinery from generating __eq__/__hash__
# at all, so the custom __eq__ below stands and __hash__ = None survives.
# With the default eq=True, frozen=True regenerates a field-based
# __hash__ *after* the class body runs, silently overriding it.
@dataclass(frozen=True, eq=False)
class Decision:
    verdict: Verdict
    action_class: ActionClass
    confidence: float
    autonomy: Autonomy
    reason: str

    @property
    def requires_verbatim_readback(self) -> bool:
        return (
            self.verdict is Verdict.CONFIRM
            and self.action_class in _VERBATIM
        )

    # Comparing a Decision to a Verdict keeps the tests and call sites
    # readable; the verdict is the thing callers branch on.
    #
    # Two consequences that invert the usual frozen-dataclass contract,
    # so read before relying on `==` here:
    #  1. `Decision == Decision` compares by *identity*, not by field
    #     values, because this __eq__ returns NotImplemented for
    #     non-Verdict operands and Python then falls back to `is`.
    #     Use `.verdict` (or compare fields explicitly) if you need
    #     structural equality.
    #  2. __hash__ is None deliberately: a hash derived from the fields
    #     would be inconsistent with this __eq__, so Decision is
    #     unhashable and cannot be a dict key or set member. That fails
    #     loudly with TypeError, which is the intent.
    def __eq__(self, other: object) -> bool:
        if isinstance(other, Verdict):
            return self.verdict is other
        return NotImplemented

    __hash__ = None  # type: ignore[assignment]


def evaluate(
    action_class: ActionClass,
    confidence: float,
    autonomy: Autonomy = Autonomy.CAUTIOUS,
) -> Decision:
    """Return the gate's decision. Never raises except on bad input."""
    if math.isnan(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be in [0,1], got {confidence!r}")

    def made(verdict: Verdict, reason: str) -> Decision:
        return Decision(verdict, action_class, confidence, autonomy, reason)

    if action_class is ActionClass.BLOCKED:
        return made(
            Verdict.REFUSE,
            "action class is blocked and is not user-loosenable",
        )

    if action_class in _NEVER_ALLOW:
        return made(
            Verdict.CONFIRM,
            f"{action_class.value} always requires confirmation "
            f"regardless of confidence ({confidence:.2f})",
        )

    if confidence < _REFUSE_THRESHOLD[autonomy]:
        return made(
            Verdict.REFUSE,
            f"confidence {confidence:.2f} below refuse threshold "
            f"{_REFUSE_THRESHOLD[autonomy]:.2f} for {autonomy.value}",
        )

    threshold = _ALLOW_THRESHOLD.get(autonomy)
    if threshold is None:
        return made(Verdict.CONFIRM, f"{autonomy.value} confirms every action")

    if confidence >= threshold:
        return made(
            Verdict.ALLOW,
            f"confidence {confidence:.2f} at or above allow threshold "
            f"{threshold:.2f} for {autonomy.value}",
        )

    return made(
        Verdict.CONFIRM,
        f"confidence {confidence:.2f} below allow threshold "
        f"{threshold:.2f} for {autonomy.value}",
    )
