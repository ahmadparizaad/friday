"""Tests for new_tab tool — navigate action."""

import pytest

from friday.agent import Agent
from friday.browser import FakeBackend, set_backend, reset_backend
from friday.policy import Autonomy
from friday.registry import REGISTRY
from friday.router import Route


class FakeRouter:
    """Router that always routes to a specific tool."""

    def __init__(self, tool="navigate_tab", confidence=0.99):
        self.tool = tool
        self.confidence = confidence

    def route(self, transcript):
        return Route(self.tool, self.confidence)


def make_agent(tmp_path, router=None, autonomy=Autonomy.CAUTIOUS):
    return Agent(
        router=router or FakeRouter(),
        confirm=lambda desc, verbatim: True,
        autonomy=autonomy,
        log_path=tmp_path / "audit.jsonl",
    )


@pytest.mark.unit
def test_navigate_tab_registered():
    """The navigate_tab tool exists and is navigate class."""
    tool = REGISTRY.get("navigate_tab")
    assert tool is not None
    assert tool.action_class.value == "navigate"


@pytest.mark.unit
def test_navigate_tab_with_browser_argument(tmp_path):
    """New tab in a specified browser (enum from BROWSERS table)."""
    backend = FakeBackend()
    set_backend(backend)
    try:
        agent = make_agent(tmp_path)
        result = agent.handle("open a new tab in chrome")
        assert result.status == "executed"
        assert "tab" in result.message.lower()
    finally:
        reset_backend()


@pytest.mark.unit
def test_navigate_tab_no_browser_uses_default(tmp_path):
    """New tab without specifying browser uses default/frontmost."""
    backend = FakeBackend()
    set_backend(backend)
    try:
        agent = make_agent(tmp_path)
        result = agent.handle("open a new tab")
        assert result.status == "executed"
        assert "tab" in result.message.lower()
    finally:
        reset_backend()


@pytest.mark.unit
@pytest.mark.parametrize("utterance", ["open the table", "open a tablet app", "create a stable"])
def test_new_tab_gate_needs_the_word_tab_not_a_substring(utterance):
    from friday.slots import SlotError, extract

    with pytest.raises(SlotError):
        extract("navigate_tab", utterance)
