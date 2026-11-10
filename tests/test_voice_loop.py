"""Voice loop tests — ARCHITECTURE.md §5.1, §9.4.

The core reads transcripts from the UI channel instead of stdin. The
property under test is the one that matters once a mic exists: a
transcript is a *command*, never an answer to a pending confirmation.
"""

import json

import pytest

from friday.policy import Autonomy
from friday.voice import VoiceLoop


class FakeAgent:
    def __init__(self, confirm_on=()):
        self.handled: list[str] = []
        self._confirm_on = set(confirm_on)
        self.confirm = None  # injected by VoiceLoop's owner in real use

    def handle(self, transcript):
        self.handled.append(transcript)

        class Outcome:
            status = "executed"
            message = "ok"

        return Outcome()


class FakeChannel:
    """Feeds lines to the loop and records what it sends."""

    def __init__(self, lines):
        self.sent: list[dict] = []
        self._lines = list(lines)

    def write(self, line: str) -> None:
        self.sent.append(json.loads(line))

    def read(self, timeout: float):
        if not self._lines:
            return None
        item = self._lines.pop(0)
        return None if item is None else json.dumps(item)

    @property
    def states(self):
        return [m["state"] for m in self.sent if m["t"] == "state"]


def loop_with(lines, agent=None):
    channel = FakeChannel(lines)
    agent = agent or FakeAgent()
    loop = VoiceLoop(agent=agent, write=channel.write, read=channel.read)
    return loop, channel, agent


def test_a_final_transcript_is_handled():
    loop, _, agent = loop_with([{"t": "final", "text": "open safari"}, None])
    loop.run()
    assert agent.handled == ["open safari"]


def test_several_transcripts_are_handled_in_order():
    loop, _, agent = loop_with(
        [
            {"t": "final", "text": "open safari"},
            {"t": "final", "text": "what time is it"},
            None,
        ]
    )
    loop.run()
    assert agent.handled == ["open safari", "what time is it"]


def test_a_partial_transcript_is_not_handled():
    """Partials are for the user to read, not for the agent to act on."""
    loop, _, agent = loop_with([{"t": "partial", "text": "open saf"}, None])
    loop.run()
    assert agent.handled == []


def test_malformed_lines_are_skipped_not_fatal():
    channel = FakeChannel([])
    channel._lines = [{"t": "nonsense"}, {"t": "final", "text": "open safari"}, None]
    agent = FakeAgent()
    loop = VoiceLoop(agent=agent, write=channel.write, read=channel.read)
    loop.run()
    assert agent.handled == ["open safari"]


def test_a_verdict_arriving_with_no_prompt_is_ignored():
    """The UI should not send one, and if it does it cannot become a
    command or crash the loop."""
    loop, _, agent = loop_with(
        [{"t": "verdict", "decision_id": "x" * 32, "approve": True}, None]
    )
    loop.run()
    assert agent.handled == []


def test_state_is_reported_around_each_utterance():
    loop, channel, _ = loop_with([{"t": "final", "text": "open safari"}, None])
    loop.run()
    assert "executing" in channel.states
    assert channel.states[-1] == "idle"


def test_failure_is_rendered_as_failed():
    class Failing(FakeAgent):
        def handle(self, transcript):
            self.handled.append(transcript)

            class Outcome:
                status = "failed"
                message = "boom"

            return Outcome()

    loop, channel, _ = loop_with(
        [{"t": "final", "text": "open safari"}, None], agent=Failing()
    )
    loop.run()
    assert channel.states[-1] == "failed"


# --- speech is not consent (§9.4) ----------------------------------------


def test_speech_during_a_confirmation_does_not_approve_it():
    """The core-side half of the rule the Swift side also enforces.

    A confirmation is answered by a verdict naming its decision, and by
    nothing else — least of all by the user saying "yes" out loud while a
    panel is open.
    """
    channel = FakeChannel([])
    agent = FakeAgent()
    loop = VoiceLoop(agent=agent, write=channel.write, read=channel.read)

    # The agent asks for confirmation; the UI replies with speech.
    channel._lines = [{"t": "final", "text": "yes do it"}, None]
    assert loop.confirm("delete_files(path='~')", verbatim=True) is False
    # And that speech was not silently promoted into a command either.
    assert agent.handled == []


def test_a_transcript_mid_confirmation_is_not_queued_as_a_command():
    """Dropping it is deliberate: queueing would run a command the user
    spoke while looking at a prompt they had not answered."""
    channel = FakeChannel([])
    agent = FakeAgent()
    loop = VoiceLoop(agent=agent, write=channel.write, read=channel.read)

    channel._lines = [
        {"t": "final", "text": "open safari"},  # spoken during the prompt
        None,  # then silence, so the prompt denies
    ]
    assert loop.confirm("delete_files(path='~')", verbatim=True) is False

    channel._lines = [None]
    loop.run()
    assert agent.handled == []


@pytest.mark.parametrize("spoken", ["yes", "yes approve", "y", "ok do it", "approve"])
def test_no_spoken_phrase_approves(spoken):
    channel = FakeChannel([])
    loop = VoiceLoop(agent=FakeAgent(), write=channel.write, read=channel.read)
    channel._lines = [{"t": "final", "text": spoken}, None]
    assert loop.confirm("delete_files(path='~')", verbatim=True) is False


# --- the confirmation round trip over the same channel --------------------


def test_a_bound_verdict_approves():
    channel = FakeChannel([])
    loop = VoiceLoop(agent=FakeAgent(), write=channel.write, read=channel.read)

    def read(timeout):
        decision = [m for m in channel.sent if m["t"] == "confirm"][-1]["decision_id"]
        return json.dumps(
            {"t": "verdict", "decision_id": decision, "approve": True}
        )

    loop._read = read
    assert loop.confirm("use_app(app='Safari')", verbatim=False) is True


def test_a_bound_denial_denies():
    channel = FakeChannel([])
    loop = VoiceLoop(agent=FakeAgent(), write=channel.write, read=channel.read)

    def read(timeout):
        decision = [m for m in channel.sent if m["t"] == "confirm"][-1]["decision_id"]
        return json.dumps(
            {"t": "verdict", "decision_id": decision, "approve": False}
        )

    loop._read = read
    assert loop.confirm("delete_files(path='~')", verbatim=True) is False


def test_an_expired_confirmation_denies_and_dismisses():
    channel = FakeChannel([])
    loop = VoiceLoop(
        agent=FakeAgent(), write=channel.write, read=channel.read, timeout=0.01
    )

    def flood(timeout):
        return json.dumps({"t": "final", "text": "still talking"})

    loop._read = flood
    assert loop.confirm("delete_files(path='~')", verbatim=True) is False
    assert any(m["t"] == "dismiss" for m in channel.sent)


def test_state_reporting_survives_a_dead_channel():
    """A dead UI must not end the session."""

    def dead(_line):
        raise BrokenPipeError("gone")

    loop = VoiceLoop(agent=FakeAgent(), write=dead, read=lambda t: None)
    loop.set_state("idle")  # must not raise


def test_run_stops_when_the_channel_closes():
    loop, _, agent = loop_with([None])
    loop.run()  # returns rather than spinning
    assert agent.handled == []


def test_on_outcome_receives_the_utterance_and_result():
    seen = []
    channel = FakeChannel([{"t": "final", "text": "open safari"}, None])
    loop = VoiceLoop(
        agent=FakeAgent(),
        write=channel.write,
        read=channel.read,
        on_outcome=lambda text, outcome: seen.append((text, outcome.status)),
    )
    loop.run()
    assert seen == [("open safari", "executed")]


def test_run_waits_for_speech_without_a_timeout():
    """Regression: run() read transcripts with the confirm timeout, and the
    channel returns None on timeout as well as on close, so two minutes of
    silence shut the core down and later speech went nowhere."""
    timeouts = []

    def read(timeout):
        timeouts.append(timeout)
        return None  # the channel closes

    VoiceLoop(agent=FakeAgent(), write=lambda line: None, read=read).run()
    assert timeouts == [None]
