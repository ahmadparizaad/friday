"""BrowserBackend tests and the shared browser fake.

tests/test_search_here.py and skills/web.search_here import
FakeBackendForBrowser and make_test_skill from here.
"""

import pytest

from friday.browser import reset_backend, set_backend
from friday.policy import ActionClass
from friday.skills.backends.base import BackendError
from friday.skills.backends.browser import BrowserBackend
from friday.skills.manifest import Postcondition, Skill, Slot, Step


class FakeBackendForBrowser:
    """A browser whose tabs are a list; the last one is the current tab."""

    def __init__(self):
        self.tabs: list[str] = []
        self.navigated: list[str] = []

    def get_current_url(self) -> str:
        return self.tabs[-1] if self.tabs else "about:blank"

    def navigate(self, url: str) -> str:
        self.navigated.append(url)
        if self.tabs:
            self.tabs[-1] = url
        else:
            self.tabs.append(url)
        return url

    def new_tab(self, url: str) -> str:
        self.tabs.append(url)
        return url


def make_test_skill() -> Skill:
    return Skill(
        id="test.browser",
        version="0.1.0",
        title="Test browser skill",
        triggers=("search {query}",),
        slots={"query": Slot(name="query", type="text", provenance="utterance")},
        action_class=ActionClass.NAVIGATE,
        steps=(Step(id="s", backend="browser", argv=("search_here", "{query}"), dry_run="x"),),
        postcondition=Postcondition(type="output_matches", value="^searched"),
        undo="none",
    )


@pytest.fixture
def fake():
    f = FakeBackendForBrowser()
    set_backend(f)
    yield f
    reset_backend()


def test_slot_value_is_only_ever_the_query_never_a_url(fake):
    fake.tabs = ["https://github.com/"]
    step = Step(id="s", backend="browser", argv=("search_here", "{query}"), dry_run="")
    BrowserBackend(make_test_skill()).run(step, {"query": "https://evil.example/"})
    assert fake.navigated == ["https://github.com/search?q=https%3A%2F%2Fevil.example%2F"]


@pytest.mark.parametrize(
    "argv",
    [("navigate", "https://evil.example/"), ("goto", "x"), ("search_here",), ("search_here", "a", "b")],
)
def test_unknown_verbs_and_wrong_arity_are_refused(fake, argv):
    step = Step(id="s", backend="browser", argv=argv, dry_run="")
    with pytest.raises(BackendError):
        BrowserBackend(make_test_skill()).run(step, {})
    assert fake.navigated == []


# --- review fixes ----------------------------------------------------------------

import subprocess

import friday.browser as browser_mod
from friday.browser import BrowserError, BrowserUnreachable


class _NoPort:
    def get_current_url(self):
        raise BrowserUnreachable("no port")

    def navigate(self, url):
        raise BrowserUnreachable("no port")

    def new_tab(self, url):
        raise BrowserUnreachable("no port")


@pytest.fixture
def no_port():
    set_backend(_NoPort())
    yield
    reset_backend()


def test_fallback_failure_is_a_browser_error_not_a_success(no_port, monkeypatch):
    monkeypatch.setattr(browser_mod, "_front_browser_url", lambda: "https://github.com/")
    monkeypatch.setattr(
        browser_mod.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom"),
    )
    with pytest.raises(BrowserError):
        browser_mod.search_here("cats")


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(["osascript"], 1), FileNotFoundError("osascript")])
def test_subprocess_errors_become_browser_errors(no_port, monkeypatch, exc):
    def boom(argv, **kw):
        raise exc

    monkeypatch.setattr(browser_mod.subprocess, "run", boom)
    with pytest.raises(BrowserError):
        browser_mod.search_here("cats")
