"""friday-ui integration tests — ARCHITECTURE.md §9.

Marked `live` because they launch the real Swift binary and touch the
window server. The protocol itself is covered headlessly in
test_protocol.py; these check the binary honours it.

Build first:  swiftc -O ui/*.swift -o build/friday-ui
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from friday.protocol import UiChannel

pytestmark = pytest.mark.live

BINARY = Path(__file__).parent.parent / "build/friday-ui"


@pytest.fixture
def ui():
    if not BINARY.exists():
        pytest.skip(f"{BINARY} not built")
    process = subprocess.Popen(
        [str(BINARY)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def channel_to(process):
    def write(line):
        process.stdin.write(line + "\n")
        process.stdin.flush()

    return UiChannel(write=write)


@pytest.mark.parametrize(
    "state", ["idle", "listening", "routing", "executing", "failed"]
)
def test_renders_every_state_without_dying(ui, state):
    channel = channel_to(ui)
    channel.send_state(state)
    time.sleep(0.2)
    assert ui.poll() is None, ui.stderr.read()


def test_survives_an_argument_taller_than_the_screen(ui):
    """A 4500-character argument previously laid out ~1200pt tall on an
    832pt screen, putting Approve/Deny off-screen — worse than
    truncating, because the decision becomes unactionable. The body
    scrolls instead (§9.2)."""
    channel = channel_to(ui)
    huge = "send_message(to='Mom', body='" + "very " * 900 + "sorry')"
    assert len(huge) > 4000
    channel.request_confirmation(huge, verbatim=True)
    time.sleep(1.0)
    assert ui.poll() is None, ui.stderr.read()


def test_ignores_messages_it_does_not_understand(ui):
    """Unknown or malformed input must not crash the UI — it renders the
    last state it understood."""
    for junk in ["{not json", "[]", '{"t":"nonsense"}', '{"no":"kind"}', ""]:
        ui.stdin.write(junk + "\n")
        ui.stdin.flush()
    time.sleep(0.4)
    assert ui.poll() is None, ui.stderr.read()


def test_never_writes_anything_but_a_verdict(ui):
    """The UI's only outbound message kind is a verdict (§4.1). It cannot
    name a tool or request an action, so nothing else may appear on
    stdout."""
    import select

    channel = channel_to(ui)
    channel.send_state("listening")
    channel.request_confirmation("use_app(app='Safari')", verbatim=False)
    time.sleep(0.6)

    ready, _, _ = select.select([ui.stdout], [], [], 0.2)
    if not ready:
        return  # nothing emitted without a click, which is correct
    line = ui.stdout.readline()
    if line.strip():
        payload = json.loads(line)
        assert payload["t"] == "verdict"
        assert set(payload) <= {"t", "decision_id", "approve"}
