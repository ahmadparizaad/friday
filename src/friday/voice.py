"""The voice loop — ARCHITECTURE.md §5.1, §9.4.

Reads transcripts from the UI channel and hands them to the Agent, and
serves confirmations over the same channel.

One reader, two purposes, and the interaction between them is the point:
while a confirmation is open, `confirm` owns the channel. A transcript
arriving then is **dropped, not queued**. Two reasons, both deliberate:

- It is not an answer. A confirmation is settled by a verdict naming its
  decision id and by nothing else, so saying "yes" at an open panel must
  not approve it (§9.4).
- It is not a command either. Queueing it would run something the user
  spoke while staring at a decision they had not yet made — the order
  they intended is unknowable, so the safe reading is that they were
  talking about the prompt, not issuing work.
"""

from __future__ import annotations

from typing import Callable, Protocol

from friday.protocol import (
    DEFAULT_TIMEOUT,
    ProtocolError,
    UiChannel,
    await_verdict,
    parse_inbound,
)


class _Agent(Protocol):
    def handle(self, transcript: str): ...


class VoiceLoop:
    """Drives the agent from spoken transcripts."""

    def __init__(
        self,
        agent: _Agent,
        write: Callable[[str], None],
        read: Callable[[float], str | None],
        timeout: float = DEFAULT_TIMEOUT,
        on_outcome: Callable[[str, object], None] | None = None,
    ):
        self._agent = agent
        self._channel = UiChannel(write=write)
        self._read = read
        self._timeout = timeout
        self._on_outcome = on_outcome

    # --- the Agent's confirm callable ------------------------------------

    def confirm(self, description: str, verbatim: bool) -> bool:
        """Ask the UI and deny unless it approves *this* decision."""
        return await_verdict(
            self._channel, self._read, description, verbatim, self._timeout
        )

    # --- the main loop ---------------------------------------------------

    def set_state(self, state: str) -> None:
        try:
            self._channel.send_state(state)
        except Exception:  # noqa: BLE001 - a dead UI must not end the session
            pass

    def run(self) -> None:
        """Handle transcripts until the channel closes."""
        while True:
            line = self._read(self._timeout)
            if line is None:
                return

            try:
                message = parse_inbound(line)
            except ProtocolError:
                continue

            if message.kind != "transcript":
                # A verdict with no prompt open answers nothing. Ignore it
                # rather than letting it become input.
                continue

            if not message.is_final:
                # A partial is the user still speaking. Acting on it would
                # run a half-heard command.
                continue

            self.set_state("executing")
            outcome = self._agent.handle(message.text)
            self.set_state("failed" if outcome.status == "failed" else "idle")
            if self._on_outcome is not None:
                self._on_outcome(message.text, outcome)
