"""Tests for close_tab tool — destructive action with confirmation."""

import pytest

from friday.agent import Agent
from friday.browser import FakeBackend, set_backend, reset_backend
from friday.policy import Autonomy
from friday.registry import REGISTRY
from friday.router import Route


class FakeRouter:
    """Router that always routes to a specific tool."""

    def __init__(self, tool="close_tab", confidence=0.99):
        self.tool = tool
        self.confidence = confidence

    def route(self, transcript):
        return Route(self.tool, self.confidence)


def make_agent(tmp_path, router=None, confirm=None, autonomy=Autonomy.CAUTIOUS):
    return Agent(
        router=router or FakeRouter(),
        confirm=confirm or (lambda desc, verbatim: True),
        autonomy=autonomy,
        log_path=tmp_path / "audit.jsonl",
    )


@pytest.mark.unit
def test_close_tab_registered():
    """The close_tab tool exists and is destructive class."""
    tool = REGISTRY.get("close_tab")
    assert tool is not None
    assert tool.action_class.value == "destructive"


@pytest.mark.unit
def test_close_tab_always_requires_confirmation(tmp_path):
    """Close tab always requires confirmation, even at high confidence."""
    backend = FakeBackend()
    set_backend(backend)
    try:
        # High confidence should not bypass confirmation for destructive action
        agent = make_agent(
            tmp_path,
            FakeRouter("close_tab", 0.99),
            confirm=lambda desc, verbatim: True,
        )
        result = agent.handle("close the current tab")
        assert result.status == "executed"
    finally:
        reset_backend()


@pytest.mark.unit
def test_close_tab_denied_when_rejected(tmp_path):
    """Close tab does not execute when user denies confirmation."""
    backend = FakeBackend()
    set_backend(backend)
    try:
        agent = make_agent(
            tmp_path,
            FakeRouter("close_tab", 0.99),
            confirm=lambda desc, verbatim: False,
        )
        result = agent.handle("close this tab")
        assert result.status == "denied"
    finally:
        reset_backend()


@pytest.mark.unit
def test_close_tab_strict_autonomy_confirms(tmp_path):
    """Close tab confirms even at STRICT autonomy (no auto-allow for destructive)."""
    backend = FakeBackend()
    set_backend(backend)
    try:
        # Even at STRICT, destructive actions that would auto-allow at PERMISSIVE
        # still confirm because destructive has a floor of CONFIRM
        agent = make_agent(
            tmp_path,
            FakeRouter("close_tab", 0.99),
            confirm=lambda desc, verbatim: True,
            autonomy=Autonomy.STRICT,
        )
        result = agent.handle("close the tab")
        assert result.status == "executed"
    finally:
        reset_backend()


# --- the trigger gate: a close verb alone must never reach a destructive tool --

@pytest.mark.unit
@pytest.mark.parametrize(
    "utterance",
    ["close the current tab", "close this tab", "Close the current tab in brew.", "shut the tab"],
)
def test_close_tab_gate_accepts_a_close_verb_and_the_word_tab(utterance):
    from friday.slots import extract

    assert extract("close_tab", utterance) == {}


@pytest.mark.unit
@pytest.mark.parametrize(
    "utterance",
    ["close safari", "close this window", "close it", "close the table", "close tablet app"],
)
def test_close_tab_gate_refuses_a_close_verb_without_the_word_tab(utterance):
    from friday.slots import SlotError, extract

    with pytest.raises(SlotError):
        extract("close_tab", utterance)
