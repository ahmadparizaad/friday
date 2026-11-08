"""The agent loop — wires §5.1 through §5.6 together.

intake -> router -> slots -> policy -> executor, with every decision
appended to the audit log regardless of outcome. A refused or
unconfirmed action is still a decision worth recording (R5).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from friday.audit import AuditLog
from friday.executor import ExecutionError, execute
from friday.policy import Autonomy, Verdict, evaluate
from friday.registry import REGISTRY
from friday.router import Route, RouteError
from friday.slots import SlotError, extract

DEFAULT_LOG = Path.home() / ".local/state/friday-local/audit.jsonl"


@dataclass(frozen=True)
class Outcome:
    status: str  # executed | refused | denied | failed
    message: str


def _describe(tool: str, arguments: dict) -> str:
    """The verbatim read-back shown at a confirmation (§9.2).

    Arguments are rendered in full. There is deliberately no truncation
    path here — a confirmation that abbreviates is worse than none.
    """
    if not arguments:
        return tool
    args = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    return f"{tool}({args})"


class Agent:
    def __init__(
        self,
        router,
        confirm: Callable[[str, bool], bool],
        autonomy: Autonomy = Autonomy.CAUTIOUS,
        log_path: Path = DEFAULT_LOG,
    ):
        self._router = router
        self._confirm = confirm
        self._autonomy = autonomy
        self._audit = AuditLog(log_path)

    def set_confirm(self, confirm: Callable[[str, bool], bool]) -> None:
        """Swap the confirmation surface.

        Voice mode builds its loop after the Agent, because the loop needs
        the Agent to drive — so the surface is injected rather than passed
        to __init__.
        """
        self._confirm = confirm

    def handle(self, transcript: str) -> Outcome:
        record = {
            "utterance": transcript,
            "tool": None,
            "arguments": {},
            "act_probability": None,
            "action_class": None,
            "verdict": None,
            "reason": "",
            "outcome": "",
        }

        try:
            route: Route = self._router.route(transcript)
        except RouteError as exc:
            return self._finish(record, "refused", f"could not route: {exc}")

        record["tool"] = route.tool
        record["act_probability"] = route.confidence

        try:
            arguments = extract(route.tool, transcript)
        except SlotError as exc:
            return self._finish(record, "refused", f"could not understand: {exc}")

        record["arguments"] = arguments

        action_class = REGISTRY[route.tool].action_class
        decision = evaluate(action_class, route.confidence, self._autonomy)
        record["action_class"] = action_class.value
        record["verdict"] = decision.verdict.value
        record["reason"] = decision.reason

        if decision.verdict is Verdict.REFUSE:
            return self._finish(record, "refused", decision.reason)

        if decision.verdict is Verdict.CONFIRM:
            approved = self._confirm(
                _describe(route.tool, arguments),
                decision.requires_verbatim_readback,
            )
            record["confirmed"] = approved
            if not approved:
                return self._finish(record, "denied", "not approved")

        try:
            result = execute(route.tool, arguments)
        except ExecutionError as exc:
            return self._finish(record, "failed", str(exc))

        return self._finish(record, "executed", result.output)

    def _finish(self, record: dict, status: str, message: str) -> Outcome:
        record["outcome"] = status
        record["message"] = message
        self._audit.append(record)
        return Outcome(status=status, message=message)
