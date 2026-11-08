"""Hosted mode — process supervision and channel management for the .app bundle.

When Friday.app (Swift) is the parent and Python is the child, this module
provides:

1. stdio_channel() — adapts stderr for logging, leaves stdout/stdin for the
   channel so stray prints/logs don't corrupt the protocol.

2. install_sigterm_exit() — catches SIGTERM and raises SystemExit so Python's
   normal atexit cleanup runs (subprocess.run waits for completion, etc.).

3. watch_parent() — polls getppid() periodically and self-terminates if
   orphaned (parent died unexpectedly, Swift crashed, etc.).

4. claim_stdout() — duplicates fd 1 (stdout channel) and redirects stdout
   to stderr so library prints and debug output don't corrupt the channel.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import threading
import time
from typing import Callable

from friday.protocol import ProtocolError


def claim_stdout() -> tuple[int, int]:
    """Prepare stdout for use as a channel.

    Duplicates fd 1 (stdout) to a new fd for the caller to use as the channel's
    write end, then redirects stdout to stderr so stray prints and library
    output go to stderr instead of corrupting the channel.

    Returns: (channel_fd, original_stdout_fd)
    """
    channel_fd = os.dup(1)
    os.dup2(2, 1)
    return channel_fd, 1


def stdio_channel(
    in_fd: int, out_fd: int
) -> tuple[Callable[[str], None], Callable[[float | None], str | None]]:
    """Build write and read callables over a pair of file descriptors.

    Args:
        in_fd: file descriptor to read from (core's stdin)
        out_fd: file descriptor to write to (core's stdout / the channel)

    Returns: (write, read)
        write(line: str) — send a line to the UI
        read(timeout: float | None) — receive a line from the UI
    """
    reader = os.fdopen(in_fd, "r", buffering=1, encoding="utf-8")

    def write(line: str) -> None:
        try:
            os.write(out_fd, (line + "\n").encode("utf-8"))
        except OSError as e:
            raise BrokenPipeError(f"Failed to write to channel: {e}") from e

    def read(timeout: float | None) -> str | None:
        import select

        if timeout is not None and timeout < 0:
            timeout = None

        ready, _, _ = select.select([in_fd], [], [], timeout)
        if not ready:
            return None

        try:
            line = reader.readline()
        except OSError as e:
            raise ProtocolError(f"Failed to read from channel: {e}") from e
        return line.rstrip("\n") if line else None

    return write, read


def install_sigterm_exit() -> None:
    """Install a SIGTERM handler that raises SystemExit for clean shutdown."""

    def on_sigterm(signum: int, frame) -> None:
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, on_sigterm)


def watch_parent(
    interval: float = 1.0, on_orphaned: Callable[[], None] | None = None
) -> None:
    """Start a daemon thread that monitors the parent process.

    If the parent dies (ppid changes to 1 or parent is gone), self-terminates.
    """
    initial_ppid = os.getppid()

    def check_parent():
        while True:
            time.sleep(interval)
            try:
                current_ppid = os.getppid()
                if current_ppid != initial_ppid or current_ppid == 1:
                    if on_orphaned:
                        on_orphaned()
                    else:
                        os.kill(os.getpid(), signal.SIGTERM)
                    return
            except Exception:
                if on_orphaned:
                    on_orphaned()
                else:
                    os.kill(os.getpid(), signal.SIGTERM)
                return

    thread = threading.Thread(target=check_parent, daemon=True)
    thread.start()
