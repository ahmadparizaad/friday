"""Hosted mode tests — ARCHITECTURE.md §4.1.

Tests for the hosted.py module that handles process supervision and channel
management when Swift is the parent and Python is the child.
"""

import os
import signal
import time

import pytest

from friday.hosted import install_sigterm_exit, stdio_channel, watch_parent


def test_stdio_channel_write_and_read():
    """stdio_channel() builds write/read over fds."""
    r_fd, w_fd = os.pipe()

    write, read = stdio_channel(in_fd=r_fd, out_fd=w_fd)

    write("hello world")

    result = read(timeout=1.0)
    assert result == "hello world", f"Expected 'hello world', got {result!r}"

    os.close(w_fd)


def test_stdio_channel_read_timeout():
    """stdio_channel.read() respects timeout."""
    r_fd, w_fd = os.pipe()
    write, read = stdio_channel(in_fd=r_fd, out_fd=w_fd)

    result = read(timeout=0.1)
    assert result is None, f"Expected None on timeout, got {result!r}"

    os.close(w_fd)


def test_stdio_channel_read_eof():
    """stdio_channel.read() returns None on EOF."""
    r_fd, w_fd = os.pipe()
    write, read = stdio_channel(in_fd=r_fd, out_fd=w_fd)

    os.close(w_fd)

    result = read(timeout=0.1)
    assert result is None, f"Expected None on EOF, got {result!r}"


def test_install_sigterm_exit():
    """install_sigterm_exit() converts SIGTERM to SystemExit."""
    install_sigterm_exit()

    with pytest.raises(SystemExit) as exc_info:
        os.kill(os.getpid(), signal.SIGTERM)

    assert exc_info.value.code == 128 + signal.SIGTERM


def test_watch_parent_smoke_test():
    """Smoke test that watch_parent() doesn't crash on startup."""
    watch_parent(interval=0.1)
    time.sleep(0.05)
    assert True
