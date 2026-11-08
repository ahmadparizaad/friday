"""The goal loop — PLAN-M6 §M6-2 (planner stubbed).

Runs a multi-step goal: observe (AX tree) → plan one step (scripted in
M6) → validate → gate → execute → postcondition check → repeat, bounded
by a Budget. Every step passes `policy.evaluate`; there is no bypass path.

Trusted: the registry, the gate, the classifier, the audit chain.
Untrusted: everything read from the screen and everything the planner
emits (`why` text is display-only and never used for any decision).
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from friday import ax as ax_mod
from friday import classify as classify_mod
from friday import postcondition as post_mod
from friday.audit import AuditLog
from friday.policy import ActionClass, Autonomy, Verdict, evaluate

DEFAULT_BUDGET_STEPS = 15
DEFAULT_BUDGET_REPLANS = 2
DEFAULT_BUDGET_WALL_SECONDS = 180
DEFAULT_BUDGET_WAITS = 3
DEFAULT_POST_WAIT_SECONDS = 2.0

# The explicit trigger word starting goal mode (M6-5). Checked before
# routing; without it the utterance takes the single-command path, so a
# goal is never started by accident.
GOAL_TRIGGER = "goal"

# Sources every goal-step argument must come from (§5.1). Screen text is
# never a source: untrusted content may influence *which* element id is
# picked, and nothing else.
ARG_SOURCES = ("observation_id", "goal_span", "allow_list", "user_answer", "profile")


class GoalError(Exception):
    """The goal could not run."""


class StepRejected(Exception):
    """validate_step refused a planner step (counts against replan budget)."""


@dataclass(frozen=True)
class Budget:
    max_steps: int = DEFAULT_BUDGET_STEPS
    max_replans_per_step: int = DEFAULT_BUDGET_REPLANS
    max_wall_seconds: float = DEFAULT_BUDGET_WALL_SECONDS
    max_consecutive_waits: int = DEFAULT_BUDGET_WAITS
    post_wait_seconds: float = DEFAULT_POST_WAIT_SECONDS


@dataclass(frozen=True)
class PlanStep:
    tool: str
    args: dict
    expect: dict
    why: str = ""
    arg_sources: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AskUser:
    question: str


@dataclass(frozen=True)
class Finish:
    summary: str


@dataclass
class StepRecord:
    step: int
    tool: str
    args: dict
    verdict: str
    outcome: str
    message: str = ""


@dataclass
class GoalRun:
    goal_id: str
    goal: str
    status: str  # running | done | failed | needs_user | cancelled
    steps: list = field(default_factory=list)
    budget_used: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GoalContext:
    """Carries the active goal into the executor.

    Only GoalRunner creates one: `executor.execute` refuses a goal-scoped
    tool unless it arrives with a GoalContext, so no single utterance can
    reach the AX tools.
    """

    goal_id: str
    snapshot_id: str
    # element id -> (role, label) in the observation the id came from.
    # The executor re-fetches a fresh table and refuses on mismatch
    # (stale-id defense, same idea as browser.py).
    expected: dict = field(default_factory=dict)
    # The backend the runner observes through. Falls back to the
    # module-level backend when absent (production wires one backend).
    backend: object = None


MIN_SPAN_CHARS = 3


def _is_span(value: str, goal: str) -> bool:
    return (
        isinstance(value, str)
        and len(value.strip()) >= MIN_SPAN_CHARS
        and value.casefold() in goal.casefold()
    )


def validate_step(
    step: PlanStep,
    obs: ax_mod.Observation,
    goal: str,
    answers: tuple = (),
    profile_values: tuple = (),
) -> dict:
    """Check a planner step before it reaches the gate (PLAN-M6 §5.1).

    Returns the action class to gate under (dynamic for ax_click).
    Raises StepRejected — never executes — when the planner returns an
    unknown tool, an id not in THIS observation, or text without allowed
    provenance.
    """
    from friday.registry import REGISTRY

    tool = REGISTRY.get(step.tool)
    if tool is None:
        raise StepRejected(f"unknown tool {step.tool!r}")
    if getattr(tool, "scope", "command") != "goal":
        raise StepRejected(f"{step.tool!r} is not a goal tool")
    if not isinstance(step.args, dict):
        raise StepRejected("args must be an object")
    if not isinstance(step.expect, dict) or len(step.expect) != 1:
        raise StepRejected("expect must be a single postcondition")

    action_class = tool.action_class
    for arg_name, spec in tool.arguments.items():
        if arg_name not in step.args:
            raise StepRejected(f"missing argument {arg_name!r}")
        value = step.args[arg_name]
        kind = spec["type"]
        source = (step.arg_sources.get(arg_name) or {}).get("source")
        if kind == "element_id":
            if source != "observation_id":
                raise StepRejected(f"{arg_name}: element ids come from the observation only")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise StepRejected(f"{arg_name}={value!r} is not an element id")
            el = obs.by_id(value)
            if el is None:
                raise StepRejected(f"element {value} is not in this observation")
            if step.tool == "ax_click":
                action_class = classify_mod.raise_floor(
                    action_class, classify_mod.classify_click(el, obs.app)
                )
            elif step.tool == "ax_type":
                if el.role == "secure_field":
                    raise StepRejected(f"cannot type into a secure field [{el.id}]")
                if el.role not in ("text_field", "text_area", "search_field", "combo_box"):
                    raise StepRejected(f"cannot type into role {el.role!r}")
        elif kind == "goal_text":
            if source not in ("goal_span", "user_answer", "profile"):
                raise StepRejected(f"{arg_name}: text needs goal_span, user_answer or profile provenance")
            if not isinstance(value, str) or not value.strip():
                raise StepRejected(f"{arg_name} is empty")
            if len(value) > 500:
                raise StepRejected(f"{arg_name} is too long")
            if "\x00" in value:
                raise StepRejected(f"{arg_name} contains NUL")
            # Provenance is verified here, never read off the planner's claim.
            if source == "goal_span" and not _is_span(value, goal):
                raise StepRejected(f"{arg_name}: text is not a span of the goal utterance")
            if source == "user_answer" and value not in answers:
                raise StepRejected(f"{arg_name}: text is not something the user answered")
            if source == "profile" and value not in profile_values:
                raise StepRejected(f"{arg_name}: text is not a known profile value")
        elif kind == "key_combo":
            if value not in ax_mod.ALLOWED_KEYS:
                raise StepRejected(f"key combo {value!r} is not allow-listed")
        else:
            raise StepRejected(f"unsupported goal argument type {kind!r}")
    extra = set(step.args) - set(tool.arguments)
    if extra:
        raise StepRejected(f"unexpected arguments {sorted(extra)}")
    # finish.summary is display-only: typed but provenance-free, and the
    # run is done only when the goal check holds, never on planner text.
    return action_class


def _describe_step(tool: str, args: dict, why: str, obs=None) -> str:
    if obs is not None and tool in ("ax_click", "ax_type") and "element" in args:
        el = obs.by_id(args["element"])
        if el is not None:
            text = f"{tool}(element={el.id} {el.role} {el.label!r})"
            if classify_mod.is_lookalike(el.label):
                text += " [lookalike label: " + classify_mod.codepoints_for_display(el.label) + "]"
            if why:
                text += f" — {why}"
            return text
    rendered = ", ".join(f"{k}={v!r}" for k, v in args.items())
    text = f"{tool}({rendered})"
    if why:
        text += f" — {why}"
    return text


# --- scripted demo ------------------------------------------------------------
#
# M6-5: the only planner is the scripted demo, so the goal text is parsed
# against a fixed template into named slots. M7 replaces this with the
# real planner behind the same `plan_next` interface.

_DEMO_RE = re.compile(
    r"find the (.+?) pdf in downloads and send it to (.+)", re.I | re.S
)


def parse_demo_goal(goal: str) -> dict | None:
    """Parse "find the *name* PDF in Downloads and send it to *address*".

    The address is a literal span of the goal text (provenance goal_span).
    Returns {"name", "address"} or None when no template matches.
    """
    m = _DEMO_RE.search((goal or "").strip())
    if m is None:
        return None
    name = " ".join(m.group(1).split())
    address = " ".join(m.group(2).split()).rstrip(".")
    if not name or not address:
        return None
    return {"name": name, "address": address}


class ScriptedPlanner:
    """A list of step functions standing in for the model (M6).

    Each function takes (goal, obs, history, parsed) and returns a
    PlanStep | AskUser | Finish | None (None = not applicable now). The
    first non-None wins. Predicates select by labels, never by hardcoded
    ids or positions.
    """

    def __init__(self, steps: list):
        self._steps = list(steps)

    def plan_next(self, goal: str, obs, history: list) -> PlanStep | AskUser | Finish:
        parsed = parse_demo_goal(goal)
        for fn in self._steps:
            try:
                proposal = fn(goal, obs, history, parsed)
            except Exception:
                continue
            if proposal is not None:
                return proposal
        raise GoalError("scripted planner has no step for this observation")


def _finder_rows(obs, needle: str) -> list:
    needle = needle.casefold()
    return [
        e
        for e in obs.elements
        if e.role in ("row", "cell", "outline_row", "static_text")
        and needle in (e.label or "").casefold()
    ]


def _already_clicked(history: list, element_id: int) -> bool:
    return any(
        s.tool == "ax_click"
        and isinstance(s.args, dict)
        and s.args.get("element") == element_id
        for s in history
    )


def demo_script(name: str, address: str) -> ScriptedPlanner:
    """The milestone demo: invoice PDF in Downloads → Mail to `address`."""

    def find_invoice(goal, obs, history, parsed):
        if obs.app.get("name") != "Finder":
            return None
        rows = _finder_rows(obs, name)
        if len(rows) > 1:
            return AskUser(f"multiple files match {name!r} — which one?")
        if not rows:
            return None
        el = rows[0]
        if _already_clicked(history, el.id):
            return None
        return PlanStep(
            tool="ax_click",
            args={"element": el.id},
            arg_sources={"element": {"source": "observation_id", "value": el.id}},
            expect={"window_title_contains": {"text": "Downloads"}},
            why=f"select {el.label}",
        )

    def open_share(goal, obs, history, parsed):
        if obs.app.get("name") != "Finder":
            return None
        if not _finder_rows(obs, name):
            return None
        shares = [e for e in obs.elements if (e.label or "").casefold() == "share"]
        if not shares:
            return None
        el = shares[0]
        if _already_clicked(history, el.id):
            return None
        return PlanStep(
            tool="ax_click",
            args={"element": el.id},
            arg_sources={"element": {"source": "observation_id", "value": el.id}},
            expect={"app_frontmost": {"name": "Mail"}},
            why="share the invoice via Mail",
        )

    def fill_to(goal, obs, history, parsed):
        if obs.app.get("name") != "Mail":
            return None
        fields = [e for e in obs.elements if (e.label or "").casefold() == "to:"]
        if not fields or (fields[0].value or "").strip():
            return None
        el = fields[0]
        return PlanStep(
            tool="ax_type",
            args={"element": el.id, "text": address},
            arg_sources={
                "element": {"source": "observation_id", "value": el.id},
                "text": {"source": "goal_span", "value": address},
            },
            expect={"element_value": {"label": "To:", "contains": address}},
            why=f"address the mail to {address}",
        )

    def press_send(goal, obs, history, parsed):
        if obs.app.get("name") != "Mail":
            return None
        to = [e for e in obs.elements if (e.label or "").casefold() == "to:"]
        if not to or address.casefold() not in (to[0].value or "").casefold():
            return None
        sends = [e for e in obs.elements if (e.label or "").casefold() == "send"]
        if not sends:
            return None
        el = sends[0]
        if _already_clicked(history, el.id):
            return None  # Send is irreversible: never propose it twice
        return PlanStep(
            tool="ax_click",
            args={"element": el.id},
            arg_sources={"element": {"source": "observation_id", "value": el.id}},
            expect={"element_appears": {"label": "Message sent"}},
            why="send the mail (always confirms)",
        )

    def sent_done(goal, obs, history, parsed):
        # After Send the compose closes and the main window shows a
        # banner — no Send button remains, so this is the only path.
        if obs.app.get("name") != "Mail":
            return None
        if any("message sent" in (e.label or "").casefold() for e in obs.elements):
            return Finish(summary=f"mail to {address} sent")
        return None

    def finish(goal, obs, history, parsed):
        if obs.app.get("name") != "Mail":
            return None
        to = [e for e in obs.elements if (e.label or "").casefold() == "to:"]
        if to and address.casefold() in (to[0].value or "").casefold():
            return Finish(summary=f"mail to {address} ready at the Send confirm")
        return None

    return ScriptedPlanner([sent_done, find_invoice, open_share, fill_to, press_send, finish])


def return_key_class(action_class: ActionClass, obs: ax_mod.Observation) -> ActionClass:
    """Return activates a default button we cannot see: use the focused
    element's class when known, else assume the worst non-destructive."""
    focused = [e for e in obs.elements if e.focused]
    if focused:
        bumped = classify_mod.classify_click(focused[0], obs.app)
    else:
        bumped = ActionClass.MUTATE_REMOTE
    return classify_mod.raise_floor(action_class, bumped)


class GoalRunner:
    """Runs goals through observe → plan → gate → execute → check."""

    def __init__(
        self,
        backend=None,
        confirm: Callable[[str, bool], bool] | None = None,
        ask: Callable[[str], str | None] | None = None,
        autonomy: Autonomy = Autonomy.CAUTIOUS,
        budget: Budget | None = None,
        log_path: Path | None = None,
    ):
        self._backend = backend or ax_mod.HelperAXBackend()
        self._confirm = confirm or (lambda description, verbatim: False)
        self._ask = ask
        self._autonomy = autonomy
        self._budget = budget or Budget()
        self._audit = AuditLog(log_path) if log_path is not None else None
        self._cancelled: set[str] = set()
        self._active_id: str | None = None

    def cancel(self, goal_id: str) -> None:
        self._cancelled.add(goal_id)

    def cancel_active(self) -> None:
        """Cancel the currently running goal, if any (UI stop control)."""
        if self._active_id is not None:
            self._cancelled.add(self._active_id)

    @property
    def cancelled_ids(self) -> set[str]:
        return set(self._cancelled)

    def _log(self, record: dict) -> None:
        if self._audit is not None:
            self._audit.append(record)

    def run(
        self,
        goal: str,
        planner=None,
        goal_done: dict | None = None,
    ) -> GoalRun:
        goal_id = uuid.uuid4().hex[:12]
        run = GoalRun(goal_id=goal_id, goal=goal, status="running")
        self._active_id = goal_id
        try:
            return self._run_inner(goal, planner, goal_done, run, goal_id)
        finally:
            self._active_id = None

    def _run_inner(self, goal, planner, goal_done, run: GoalRun, goal_id: str) -> GoalRun:

        def log(record: dict) -> None:
            # The goal utterance rides with every step record (like the
            # agent's "utterance"); screen contents never do — only the
            # obs digest plus acted-on labels.
            record.setdefault("goal", goal)
            self._log(record)
        t0 = time.monotonic()
        replans = 0
        waits = 0
        history: list[StepRecord] = []
        answers: list[str] = []

        if planner is None:
            parsed = parse_demo_goal(goal)
            if parsed is None:
                run.status = "failed"
                run.budget_used = {"steps": 0, "replans": 0, "seconds": 0.0}
                return run
            planner = demo_script(parsed["name"], parsed["address"])

        try:
            trusted = self._backend.ping().get("trusted", False)
        except Exception:
            trusted = False
        if not trusted:
            run.status = "needs_user"
            run.budget_used = {"steps": 0, "replans": 0, "seconds": 0.0}
            log(
                {
                    "goal_id": goal_id,
                    "goal": goal,
                    "outcome": "needs_user",
                    "message": "Friday needs Accessibility permission",
                }
            )
            return run

        while True:
            if goal_id in self._cancelled:
                run.status = "cancelled"
                break
            elapsed = time.monotonic() - t0
            if elapsed > self._budget.max_wall_seconds:
                run.status = "failed"
                history.append(
                    StepRecord(len(history), "", {}, "refuse", "failed", "budget: wall clock exhausted")
                )
                break
            if len(history) >= self._budget.max_steps:
                run.status = "failed"
                break
            # Cancel takes effect before the next step starts. The
            # postcondition wait below is bounded by budget.post_wait_seconds.
            try:
                obs = self._backend.snapshot()
            except ax_mod.NotTrusted:
                run.status = "needs_user"
                break
            except Exception as exc:
                run.status = "failed"
                history.append(
                    StepRecord(len(history), "ax_snapshot", {}, "refuse", "failed", str(exc))
                )
                break

            try:
                proposal = planner.plan_next(goal, obs, history)
            except GoalError as exc:
                run.status = "failed"
                history.append(
                    StepRecord(len(history), "", {}, "refuse", "failed", str(exc))
                )
                break

            if isinstance(proposal, AskUser):
                answer = self._ask(proposal.question) if self._ask else None
                if answer is None:
                    run.status = "needs_user"
                    history.append(
                        StepRecord(len(history), "ask_user", {"question": proposal.question},
                                   "confirm", "needs_user", proposal.question)
                    )
                    break
                answers.append(answer)
                history.append(
                    StepRecord(len(history), "ask_user", {"question": proposal.question},
                               "allow", "executed", answer)
                )
                replans = 0
                continue

            if isinstance(proposal, Finish):
                if goal_done is not None:
                    try:
                        fresh = self._backend.snapshot()
                        ok = post_mod.check(goal_done, fresh)
                    except Exception:
                        ok = False
                    if not ok:
                        run.status = "failed"
                        history.append(
                            StepRecord(len(history), "finish", {}, "refuse", "failed",
                                       "final postcondition not satisfied")
                        )
                        break
                run.status = "done"
                history.append(
                    StepRecord(len(history), "finish", {}, "allow", "executed", proposal.summary)
                )
                break

            if not isinstance(proposal, PlanStep):
                replans += 1
                if replans > self._budget.max_replans_per_step:
                    run.status = "failed"
                    break
                continue

            step_n = len(history)
            try:
                action_class = validate_step(proposal, obs, goal, answers=tuple(answers))
            except StepRejected as exc:
                replans += 1
                history.append(
                    StepRecord(step_n, proposal.tool, dict(proposal.args), "refuse", "rejected", str(exc))
                )
                log(
                    {
                        "goal_id": goal_id, "step": step_n, "tool": proposal.tool,
                        "arguments": dict(proposal.args), "verdict": "refuse",
                        "outcome": "rejected", "message": str(exc),
                        "obs_digest": ax_mod.observation_digest(obs),
                    }
                )
                if replans > self._budget.max_replans_per_step:
                    run.status = "failed"
                    break
                continue

            if proposal.tool == "press_key" and proposal.args.get("combo") == "return":
                action_class = return_key_class(action_class, obs)

            decision = evaluate(action_class, 1.0, self._autonomy)
            verdict = decision.verdict
            if verdict is Verdict.REFUSE:
                run.status = "failed"
                history.append(
                    StepRecord(step_n, proposal.tool, dict(proposal.args), "refuse", "refused", decision.reason)
                )
                log(
                    {
                        "goal_id": goal_id, "step": step_n, "tool": proposal.tool,
                        "arguments": dict(proposal.args), "verdict": "refuse",
                        "outcome": "refused", "obs_digest": ax_mod.observation_digest(obs),
                    }
                )
                break

            if verdict is Verdict.CONFIRM:
                approved = self._confirm(
                    _describe_step(proposal.tool, proposal.args, proposal.why, obs),
                    decision.requires_verbatim_readback,
                )
                if not approved:
                    run.status = "failed"
                    history.append(
                        StepRecord(step_n, proposal.tool, dict(proposal.args), "confirm", "denied", "not approved")
                    )
                    log(
                        {
                            "goal_id": goal_id, "step": step_n, "tool": proposal.tool,
                            "arguments": dict(proposal.args), "verdict": "confirm",
                            "outcome": "denied", "obs_digest": ax_mod.observation_digest(obs),
                        }
                    )
                    break

            ctx = GoalContext(
                goal_id=goal_id,
                snapshot_id=obs.snapshot_id,
                expected={e.id: (e.role, e.label) for e in obs.elements},
                backend=self._backend,
            )
            try:
                from friday import executor as exec_mod

                result = exec_mod.execute(proposal.tool, dict(proposal.args), ctx)
                outcome, message = "executed", result.output
            except Exception as exc:
                replans += 1
                history.append(
                    StepRecord(step_n, proposal.tool, dict(proposal.args),
                               verdict.value, "failed", str(exc))
                )
                log(
                    {
                        "goal_id": goal_id, "step": step_n, "tool": proposal.tool,
                        "arguments": dict(proposal.args), "verdict": verdict.value,
                        "outcome": "failed", "message": str(exc),
                        "obs_digest": ax_mod.observation_digest(obs),
                    }
                )
                if replans > self._budget.max_replans_per_step:
                    run.status = "failed"
                    break
                continue

            acted = [
                getattr(obs.by_id(proposal.args.get("element", -1)), "label", ""),
            ] if "element" in proposal.args else []
            log(
                {
                    "goal_id": goal_id, "step": step_n, "tool": proposal.tool,
                    "arguments": dict(proposal.args), "verdict": verdict.value,
                    "outcome": outcome, "message": message,
                    "obs_digest": ax_mod.observation_digest(obs),
                    "acted_labels": [a for a in acted if a],
                }
            )

            ok = post_mod.wait_for(
                proposal.expect,
                self._backend.snapshot,
                timeout=self._budget.post_wait_seconds,
            )
            if ok:
                waits = 0
                replans = 0
                history.append(
                    StepRecord(step_n, proposal.tool, dict(proposal.args), verdict.value, outcome, message)
                )
                continue
            waits += 1
            if waits > self._budget.max_consecutive_waits:
                run.status = "failed"
                history.append(
                    StepRecord(step_n, proposal.tool, dict(proposal.args),
                               verdict.value, "failed", "postcondition not satisfied")
                )
                break
            replans += 1
            history.append(
                StepRecord(step_n, proposal.tool, dict(proposal.args),
                           verdict.value, "wait", "postcondition not yet satisfied")
            )
            if replans > self._budget.max_replans_per_step:
                run.status = "failed"
                break

        run.steps = history
        run.budget_used = {
            "steps": len(history),
            "replans": replans,
            "seconds": round(time.monotonic() - t0, 3),
        }
        return run
