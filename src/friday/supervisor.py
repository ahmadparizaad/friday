"""Owns the UI process and adapts its channel — §4.1, §9.

The Agent asks for `confirm(description, verbatim) -> bool`. This turns
that into a round trip over the channel: send the confirmation, wait for
a verdict bound to that decision, and treat everything else as a denial.

Everything that is not an explicit, correctly-bound approval denies:
silence, a stale verdict, a transcript, malformed input, a closed pipe.
That is §9.4's "timeout denies" generalised — the only way to approve is
to say so about the decision actually on screen.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from friday.protocol import (
    DEFAULT_TIMEOUT,
    ProtocolError,
    UiChannel,
    await_verdict,
)

BINARY = Path(__file__).parent.parent.parent / "build/friday-ui"


class UiSupervisor:
    """Adapts the UI channel to the Agent's confirm callable."""

    def __init__(
        self,
        write: Callable[[str], None],
        read: Callable[[float], str | None],
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._channel = UiChannel(write=write)
        self._read = read
        self._timeout = timeout

    def set_state(self, state: str) -> None:
        self._channel.send_state(state)

    def confirm(self, description: str, verbatim: bool) -> bool:
        """Ask the UI, and deny unless it explicitly approves *this*
        decision."""
        return await_verdict(
            self._channel, self._read, description, verbatim, self._timeout
        )


def spawn(binary: Path = BINARY, timeout: float = DEFAULT_TIMEOUT):
    """Launch the UI and return (supervisor, process).

    Raises FileNotFoundError if the binary is missing, rather than
    silently falling back to a terminal prompt — a confirmation surface
    is not something to substitute without the user knowing.
    """
    if not Path(binary).exists():
        raise FileNotFoundError(
            f"{binary} not built. Run: swiftc -O ui/*.swift -o build/friday-ui"
        )

    process = subprocess.Popen(
        [str(binary)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def write(line: str) -> None:
        if process.stdin is None or process.poll() is not None:
            raise ProtocolError("UI process is gone")
        process.stdin.write(line + "\n")
        process.stdin.flush()

    def read(timeout: float) -> str | None:
        import select

        if process.stdout is None:
            return None
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            return None
        line = process.stdout.readline()
        return line or None

    return UiSupervisor(write=write, read=read, timeout=timeout), process


def spawn_pipes(binary: Path = BINARY):
    """Launch the UI and return (write, read, process).

    For callers that build their own channel over the same pipes — the
    voice loop needs one reader serving both transcripts and verdicts, so
    it cannot share a UiSupervisor that is also reading.
    """
    supervisor, process = spawn(binary=binary)
    return supervisor._channel._write, supervisor._read, process
