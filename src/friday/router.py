"""Route an utterance to a tool — ARCHITECTURE.md §5.2.

One laya `predict` call carries one yes/no question per tool, batched,
so routing costs roughly one round trip regardless of tool count.

Every answer is scored against a name that must already be in the
registry; the highest scorer wins, and if none clears REFUSE_BELOW the
utterance is refused. That is what makes the R1 guarantee structural
rather than a matter of trusting the model: a tool outside the registry
is not "unlikely", it is unrepresentable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from friday.policy import unreachable_refuse_thresholds
from friday.registry import REGISTRY

# Pinned checkpoint. The first load downloads it; later inference is
# local. Treat the download as a supply-chain boundary (R9) — pin the
# revision so an auto-update cannot silently swap weights.
CHECKPOINT = "aac6fef/laya-mlx"
REVISION = "20aed815fc6acde75733882e7ec0e3f28aeb9717"


class RouteError(Exception):
    """The prediction could not be turned into a usable route."""


@dataclass(frozen=True)
class Route:
    tool: str
    confidence: float
    # The next-best tools above REFUSE_BELOW, best first. The agent tries
    # them only when this route's own slot grammar rejects the utterance.
    alternatives: tuple[Route, ...] = ()


# How many runner-up candidates a route carries.
MAX_ALTERNATIVES = 2


# Below this, no tool is a good enough match to act on.
REFUSE_BELOW = 0.02

# This floor runs *before* the policy gate, so raising it above a policy
# refuse threshold would make that threshold unreachable dead code and
# hand the router a decision policy is supposed to own. Checked at import
# so a bad tune fails immediately, in production, not only under test.
_unreachable = unreachable_refuse_thresholds(REFUSE_BELOW)
if _unreachable:
    raise RuntimeError(
        f"REFUSE_BELOW={REFUSE_BELOW} exceeds policy refuse thresholds "
        f"{_unreachable}, making them unreachable. Lower this floor or "
        "raise those."
    )


def build_questions() -> dict:
    """One yes/no question per tool, batched into a single predict call.

    Not a single wide `choice`: a choice must distribute probability
    across its options, so an out-of-scope utterance still elects a
    winner — measured as every wrong route landing on the same "generic"
    option. Independent nouls let every tool score low, which is what a
    request we cannot serve should look like.

    Measured on the dev corpus: choice 23/36, per-tool nouls 36/36
    (docs/ROUTING.md).
    """
    return {
        name: {"type": "noul", "instructions": tool.description}
        for name, tool in REGISTRY.items()
    }


def parse_prediction(prediction: dict) -> Route:
    """Validate laya's output and turn it into a Route.

    Confidence is the winning tool's P(true), not `act_probability`:
    the latter measured constant 1.0 on this checkpoint across correct
    routes, wrong routes and gibberish (docs/M1-MEASUREMENTS.md §2), so
    gating on it would treat every route as certain.

    A score below REFUSE_BELOW means no tool matched: raise rather than
    return the least-bad one.
    """
    answers = prediction.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise RouteError("prediction has no answers")

    # Every answer key must name a registry tool, because build_questions
    # asks exactly one question per tool. If you add a non-tool question
    # to that batch (a follow-up noul, say), special-case it here too —
    # otherwise this raises on *every* call and routing stops entirely,
    # rather than just ignoring the extra field.
    scores: dict[str, float] = {}
    for name, answer in answers.items():
        if name not in REGISTRY:
            raise RouteError(f"answer for unknown tool {name!r}")
        if not isinstance(answer, dict) or "noul" not in answer:
            raise RouteError(f"answer for {name!r} is not a noul")
        value = answer["noul"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise RouteError(f"noul for {name!r} is not a number: {value!r}")
        value = float(value)
        if math.isnan(value) or not 0.0 <= value <= 1.0:
            raise RouteError(f"noul for {name!r} out of range: {value!r}")
        scores[name] = value

    # Ties break toward registry order (dict insertion order).
    tool = max(scores, key=scores.get)
    confidence = scores[tool]
    if confidence < REFUSE_BELOW:
        raise RouteError(
            f"no tool matched: best was {tool} at {confidence:.3f}, "
            f"below {REFUSE_BELOW}"
        )

    runners_up = sorted(
        (name for name in scores if name != tool and scores[name] >= REFUSE_BELOW),
        key=lambda name: -scores[name],
    )[:MAX_ALTERNATIVES]
    return Route(
        tool=tool,
        confidence=confidence,
        alternatives=tuple(Route(name, scores[name]) for name in runners_up),
    )


class Router:
    """Holds the loaded model. Load once and keep it alive — model load
    is excluded from published latency and would dominate otherwise."""

    def __init__(self, checkpoint: str = CHECKPOINT, revision: str | None = REVISION):
        import laya_mlx as laya

        kwargs = {"revision": revision} if revision else {}
        self._agent = laya.load(checkpoint, **kwargs)
        self._questions = build_questions()

    def route(self, transcript: str) -> Route:
        # The transcript is passed as *state* — data the model is shown,
        # never a prompt it is asked to follow (§5.1).
        prediction = self._agent.predict(transcript, self._questions)
        return parse_prediction(prediction)
