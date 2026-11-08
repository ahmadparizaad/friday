"""The UI channel — ARCHITECTURE.md §4.1, §9.

The core is headless. `friday-ui` (Swift) owns the mic and the notch,
and talks to the core over a narrow, typed channel.

Inbound, the UI can send exactly two things:

    {"t": "final",   "text": "open safari"}
    {"t": "verdict", "decision_id": "...", "approve": true}

It cannot name a tool, supply an argument, or request an action. This is
what makes the merged mic+UI process acceptable (§4.1): a compromised UI
can deny anything, and can approve a decision the core *already chose to
prompt for*, but it cannot originate one.

Outbound, the core sends display state and confirmation requests.

Verdict binding: a verdict is valid only for the decision currently
awaited, is single-use, and expires. Without that, a stale or replayed
`approve` could land on a later, more dangerous action than the one the
user actually read.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable

# A transcript is speech, not a payload. Anything longer is not a
# plausible utterance and is refused before it reaches the router.
MAX_LINE = 4096

# How long a confirmation stays answerable. On expiry the answer is
# *deny* — §9.4: never auto-dismiss into approval.
DEFAULT_TIMEOUT = 120.0


class ProtocolError(Exception):
    """The message was not something the UI is allowed to say."""


@dataclass(frozen=True)
class Inbound:
    kind: str  # "transcript" | "verdict"
    text: str | None = None
    decision_id: str | None = None
    approve: bool | None = None
    # Partial transcripts exist so the user can watch their words appear.
    # Acting on one would execute a half-heard command, so the core checks
    # this rather than trusting the UI to withhold them.
    is_final: bool = True


def parse_inbound(line: str) -> Inbound:
    """Parse one line from the UI, or raise.

    Deliberately a whitelist of two shapes rather than a dispatch on an
    arbitrary type field: an unrecognised message is an error, not an
    extension point.
    """
    if len(line) > MAX_LINE:
        raise ProtocolError(f"line exceeds {MAX_LINE} bytes")
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(f"not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("not a JSON object")

    kind = payload.get("t")

    if kind in ("final", "partial"):
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ProtocolError("transcript has no text")
        return Inbound(kind="transcript", text=text, is_final=(kind == "final"))

    if kind == "verdict":
        decision_id = payload.get("decision_id")
        approve = payload.get("approve")
        if not isinstance(decision_id, str) or not decision_id:
            raise ProtocolError("verdict has no decision_id")
        # Ids we issue are hex. Reject anything else here, because
        # secrets.compare_digest raises TypeError on non-ASCII strings —
        # a hostile UI could otherwise crash the resolve path instead of
        # being denied.
        if not decision_id.isascii():
            raise ProtocolError("decision_id is not ASCII")
        if not isinstance(approve, bool):
            raise ProtocolError("verdict has no boolean approve")
        return Inbound(kind="verdict", decision_id=decision_id, approve=approve)

    raise ProtocolError(f"unsupported message {kind!r}")


@dataclass
class PendingConfirmation:
    """One awaited confirmation, bound to a single-use id."""

    description: str
    verbatim: bool
    timeout: float = DEFAULT_TIMEOUT
    decision_id: str = field(default_factory=lambda: secrets.token_hex(16))
    created: float = field(default_factory=time.monotonic)
    answered: bool = False

    # A timeout is a denial, never an approval (§9.4).
    outcome_on_timeout: bool = False

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.created) > self.timeout

    def resolve(self, decision_id: str, approve: bool) -> bool:
        """Apply a verdict to this confirmation, or raise.

        Order matters: check identity before expiry, so a verdict for the
        *wrong* decision is reported as such rather than as a timeout.
        """
        # compare_digest raises TypeError on non-ASCII, so validate before
        # calling it: this is reached directly as well as via
        # parse_inbound, and a crash here would take down the agent
        # mid-confirmation instead of answering "no".
        if not isinstance(decision_id, str) or not decision_id.isascii():
            raise ProtocolError("decision_id is not an ASCII string")
        if not secrets.compare_digest(decision_id, self.decision_id):
            raise ProtocolError(
                f"not awaiting decision {decision_id!r}"
            )
        if self.answered:
            raise ProtocolError("decision already answered")
        if self.expired:
            self.answered = True
            raise ProtocolError("confirmation expired; treated as denied")
        self.answered = True
        return approve


class UiChannel:
    """Core side of the channel.

    Holds at most one pending confirmation: two prompts collapsed into
    one would let a user approve something they did not read (§9.4).
    """

    def __init__(self, write: Callable[[str], None]):
        self._write = write
        self._pending: PendingConfirmation | None = None

    def send_state(self, state: str, **extra) -> None:
        self._emit({"t": "state", "state": state, **extra})

    def request_confirmation(
        self, description: str, verbatim: bool, timeout: float = DEFAULT_TIMEOUT
    ) -> PendingConfirmation:
        if self._pending is not None and not self._pending.answered:
            if not self._pending.expired:
                raise ProtocolError("a confirmation is already pending")

        pending = PendingConfirmation(description, verbatim, timeout=timeout)
        self._pending = pending
        # The description goes whole. There is no truncation path here:
        # a confirmation that abbreviates manufactures consent (§9.2).
        self._emit(
            {
                "t": "confirm",
                "decision_id": pending.decision_id,
                "description": description,
                "verbatim": verbatim,
            }
        )
        return pending

    def resolve(self, decision_id: str, approve: bool) -> bool:
        pending = self._pending
        if pending is None:
            raise ProtocolError("no confirmation is pending")
        result = pending.resolve(decision_id, approve)
        self._pending = None
        return result

    def dismiss(self) -> None:
        """Tell the UI to close the panel, and stop awaiting it.

        Needed when the core resolves a confirmation without a click —
        an expiry, say. Otherwise the panel stays on screen still showing
        Approve/Deny for a decision that has already been denied, and a
        click on it does nothing with no explanation. A surface that
        invites interaction it will ignore is exactly the trust problem
        this UI exists to avoid (§9.2).
        """
        self._pending = None
        self._emit({"t": "dismiss"})

    def _emit(self, payload: dict) -> None:
        self._write(json.dumps(payload, separators=(",", ":")))


def await_verdict(
    channel: UiChannel,
    read: Callable[[float], str | None],
    description: str,
    verbatim: bool,
    timeout: float = DEFAULT_TIMEOUT,
) -> bool:
    """Request a confirmation and wait for a verdict bound to it.

    The single implementation of the consent round trip. It lived in two
    places — the supervisor's stdin loop and the voice loop — which is how
    a hardening fix ends up applied to one copy and not the other. The
    logic here is small and every branch of it is load-bearing, so it gets
    one home.

    Denies on: expiry, silence, a closed pipe, malformed input that never
    resolves, a verdict for another decision, and a replay. Approves only
    on an explicit, correctly-bound approval.
    """
    pending = channel.request_confirmation(description, verbatim, timeout=timeout)

    def deny() -> bool:
        try:
            channel.resolve(pending.decision_id, approve=False)
        except ProtocolError:
            pass
        try:
            # Close the panel: it is still on screen offering Approve/Deny
            # for a decision that is now settled.
            channel.dismiss()
        except Exception:  # noqa: BLE001 - a dead UI cannot be told anything
            pass
        return False

    while True:
        # The decision's own expiry bounds the total wait. Without it, a UI
        # streaming junk or transcripts re-arms the read timeout every
        # iteration and holds the prompt open forever — "timeout denies"
        # would then fire only on a closed pipe, not a busy one (§9.4).
        if pending.expired:
            return deny()

        # Never block past the deadline: a full-length read would overshoot
        # it and leave the prompt open after it expired.
        remaining = max(0.0, pending.timeout - (time.monotonic() - pending.created))
        line = read(min(timeout, remaining))
        if line is None:
            return deny()

        try:
            message = parse_inbound(line)
        except ProtocolError:
            continue  # garbage is not an answer either way

        if message.kind != "verdict":
            # Speech. Not consent, and not a queued command: the user was
            # looking at a decision they had not made, so the order they
            # intended is unknowable (§9.4).
            continue

        try:
            return channel.resolve(message.decision_id, message.approve)
        except ProtocolError:
            # Wrong, stale or replayed id. Not an answer to what is on
            # screen, so it cannot approve it.
            return deny()
