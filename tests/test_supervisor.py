"""UI supervisor tests — ARCHITECTURE.md §4.1, §9.

The supervisor owns the UI subprocess and adapts its channel to the
`confirm(description, verbatim) -> bool` callable the Agent expects.
Tested against a fake process, so no display and no Swift.
"""

import json

import pytest

from friday.protocol import ProtocolError
from friday.supervisor import UiSupervisor


class FakeUi:
    """Stands in for the Swift process.

    `replies` is a list of dicts to feed back as if the user clicked; a
    None entry means "say nothing" (the user walked away).
    """

    def __init__(self, replies=()):
        self.sent: list[dict] = []
        self._replies = list(replies)

    def write(self, line: str) -> None:
        self.sent.append(json.loads(line))

    def read(self, timeout: float):
        if not self._replies:
            return None
        reply = self._replies.pop(0)
        return None if reply is None else json.dumps(reply)

    @property
    def confirmations(self):
        return [m for m in self.sent if m["t"] == "confirm"]

    @property
    def states(self):
        return [m["state"] for m in self.sent if m["t"] == "state"]


@pytest.fixture
def no_hang():
    """Fail a confirmation test instead of letting it hang forever.

    A regression in the loop's expiry bound shows up as a test run that
    never finishes — in CI that reads as an infrastructure problem rather
    than a bug. This turns it into a normal failure.
    """
    import signal

    def on_timeout(*_):
        raise TimeoutError("confirm() did not terminate in 10s")

    previous = signal.signal(signal.SIGALRM, on_timeout)
    signal.alarm(10)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def supervisor_with(replies=()):
    ui = FakeUi(replies)
    return UiSupervisor(write=ui.write, read=ui.read), ui


def test_approval_returns_true():
    supervisor, ui = supervisor_with()
    ui._replies = [None]  # placeholder; replaced below

    # Build the reply against the decision_id the supervisor actually issues.
    def read(timeout):
        decision = ui.confirmations[-1]["decision_id"]
        return json.dumps({"t": "verdict", "decision_id": decision, "approve": True})

    supervisor._read = read
    assert supervisor.confirm("use_app(app='Safari')", verbatim=False) is True


def test_denial_returns_false():
    supervisor, ui = supervisor_with()

    def read(timeout):
        decision = ui.confirmations[-1]["decision_id"]
        return json.dumps({"t": "verdict", "decision_id": decision, "approve": False})

    supervisor._read = read
    assert supervisor.confirm("delete_files(path='~')", verbatim=True) is False


def test_silence_denies(no_hang):
    """The user walked away. §9.4: timeout denies, never approves."""
    supervisor, _ = supervisor_with(replies=[None])
    assert supervisor.confirm("delete_files(path='~')", verbatim=True) is False


def test_a_verdict_for_another_decision_denies():
    """A replayed or stale approve must not be honoured (§4.1)."""
    supervisor, _ = supervisor_with(
        replies=[{"t": "verdict", "decision_id": "stolen", "approve": True}]
    )
    assert supervisor.confirm("delete_files(path='~')", verbatim=True) is False


def test_a_transcript_arriving_mid_confirmation_does_not_approve():
    """Speech is not consent. Only a verdict answers a confirmation."""
    supervisor, ui = supervisor_with(
        replies=[{"t": "final", "text": "yes go ahead"}, None]
    )
    assert supervisor.confirm("delete_files(path='~')", verbatim=True) is False


def test_malformed_input_denies_rather_than_crashing():
    supervisor, _ = supervisor_with(replies=[{"t": "nonsense"}, None])
    assert supervisor.confirm("use_app(app='Safari')", verbatim=False) is False


def test_confirmation_is_sent_with_full_description():
    supervisor, ui = supervisor_with(replies=[None])
    long_body = "I'll be late, " + "very " * 300 + "sorry"
    supervisor.confirm(f"send_message(body={long_body!r})", verbatim=True)
    sent = ui.confirmations[-1]
    assert long_body in sent["description"]
    assert sent["verbatim"] is True


def test_state_is_reported_for_rendering():
    supervisor, ui = supervisor_with()
    supervisor.set_state("listening")
    supervisor.set_state("executing")
    assert ui.states == ["listening", "executing"]


def test_a_hostile_decision_id_denies_rather_than_crashing():
    """A non-ASCII decision_id must not escape as a TypeError.

    compare_digest raises on non-ASCII, so before this was guarded a
    hostile UI could take the agent down mid-confirmation rather than
    being told no.
    """
    supervisor, _ = supervisor_with(
        replies=[{"t": "verdict", "decision_id": "héllo", "approve": True}, None]
    )
    assert supervisor.confirm("delete_files(path='~')", verbatim=True) is False


def test_streaming_junk_cannot_hold_a_confirmation_open_forever(no_hang):
    """A hostile UI must not be able to hang the agent.

    The loop skips non-verdict messages, so a UI streaming valid
    transcripts would spin without ever resolving. The confirmation's own
    expiry has to bound the wait.
    """
    calls = {"n": 0}

    def flood(timeout):
        calls["n"] += 1
        return json.dumps({"t": "final", "text": "noise"})

    ui = FakeUi()
    # A tiny expiry so the bound is reached quickly. The assertion is
    # about *terminating*, not about wall-clock time: timing the loop made
    # this flaky under coverage instrumentation, which is exactly the kind
    # of assertion that fails for reasons unrelated to the property.
    supervisor = UiSupervisor(write=ui.write, read=flood, timeout=0.01)
    assert supervisor.confirm("delete_files(path='~')", verbatim=True) is False
    assert calls["n"] > 1, "should have read more than once"
    assert any(m["t"] == "dismiss" for m in ui.sent), "panel left open"


def test_a_later_prompt_is_unaffected_by_an_expired_one():
    """Prompt A expires unanswered, B is issued, then A's verdict
    arrives. A must not resolve B, and B must stay answerable."""
    from friday.protocol import UiChannel

    sent = []
    channel = UiChannel(write=sent.append)
    a = channel.request_confirmation("use_app(app='Safari')", verbatim=False, timeout=0.0)
    b = channel.request_confirmation("delete_files(path='~')", verbatim=True)

    with pytest.raises(ProtocolError):
        channel.resolve(a.decision_id, approve=True)
    assert channel.resolve(b.decision_id, approve=False) is False


def test_a_denied_confirmation_closes_its_panel():
    """The panel must not linger showing Approve/Deny for a settled
    decision — a surface that invites clicks it will ignore undermines
    the trust the panel exists to build (§9.2)."""
    supervisor, ui = supervisor_with(replies=[None])
    supervisor.confirm("delete_files(path='~')", verbatim=True)
    assert any(m["t"] == "dismiss" for m in ui.sent), ui.sent


def test_a_stale_verdict_also_closes_the_panel():
    supervisor, ui = supervisor_with(
        replies=[{"t": "verdict", "decision_id": "stolen", "approve": True}]
    )
    supervisor.confirm("delete_files(path='~')", verbatim=True)
    assert any(m["t"] == "dismiss" for m in ui.sent), ui.sent


def test_dismiss_clears_the_pending_slot():
    """After a dismiss the next confirmation must be issuable."""
    from friday.protocol import UiChannel

    sent = []
    channel = UiChannel(write=sent.append)
    channel.request_confirmation("first", verbatim=False)
    channel.dismiss()
    channel.request_confirmation("second", verbatim=False)  # must not raise
    assert [m["t"] for m in [json.loads(s) for s in sent]] == [
        "confirm",
        "dismiss",
        "confirm",
    ]


def test_cli_state_reporting_survives_a_dead_ui():
    """A dead UI must degrade, not end the session with a traceback."""
    from friday.cli import _show

    class Dead:
        def set_state(self, state):
            raise ProtocolError("UI process is gone")

    _show(Dead(), "executing")  # must not raise
    _show(None, "executing")  # no supervisor at all


def test_the_ui_process_is_terminated_on_exit(tmp_path):
    """A leaked UI keeps a panel over the user's screen after the agent
    has gone, which they then have to hunt down and kill."""
    from friday.cli import _stop_ui
    from friday.supervisor import spawn

    fake = tmp_path / "friday-ui"
    fake.write_text("#!/bin/sh\ncat >/dev/null\n")
    fake.chmod(0o755)

    _supervisor, process = spawn(binary=fake)
    assert process.poll() is None, "should be running"
    _stop_ui(process)
    assert process.poll() is not None, "should be gone"


def test_stopping_an_already_dead_ui_is_harmless():
    from friday.cli import _stop_ui

    _stop_ui(None)  # never spawned


def test_spawn_refuses_rather_than_falling_back(tmp_path):
    """A missing UI binary must fail loudly.

    Silently falling back to the terminal would change which surface asks
    for consent without telling the user — and the terminal cannot be
    where a notch-UI user expects to look for it.
    """
    from friday.supervisor import spawn

    with pytest.raises(FileNotFoundError, match="not built"):
        spawn(binary=tmp_path / "nope")


def test_write_fails_loudly_once_the_ui_is_gone(tmp_path):
    """A dead UI must not silently swallow confirmations — a confirmation
    nobody can see is not a confirmation."""
    import subprocess

    from friday.supervisor import spawn

    fake = tmp_path / "friday-ui"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)

    supervisor, process = spawn(binary=fake, timeout=0.2)
    process.wait(timeout=5)  # it exits immediately

    with pytest.raises((ProtocolError, BrokenPipeError, ValueError)):
        for _ in range(50):  # buffers absorb the first writes
            supervisor.set_state("listening")


def test_closed_ui_denies_a_confirmation(tmp_path, no_hang):
    """If the UI dies mid-prompt, the answer is no."""
    from friday.supervisor import spawn

    fake = tmp_path / "friday-ui"
    # Accepts input, never answers: the "user walked away" shape.
    fake.write_text("#!/bin/sh\ncat >/dev/null\n")
    fake.chmod(0o755)

    supervisor, process = spawn(binary=fake, timeout=0.3)
    try:
        assert supervisor.confirm("delete_files(path='~')", verbatim=True) is False
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_confirmations_do_not_overlap():
    """§9.4: two pending confirmations never collapse into one prompt."""
    supervisor, ui = supervisor_with()
    supervisor._channel.request_confirmation("first", verbatim=False)
    with pytest.raises(ProtocolError):
        supervisor._channel.request_confirmation("second", verbatim=False)
