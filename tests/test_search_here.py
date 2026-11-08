"""search_here: search the site the current tab is on, via the closed templates."""

from __future__ import annotations

import subprocess

import pytest

from friday import browser as b
from friday.browser import BrowserError, reset_backend, set_backend, site_for_url
from friday.skills.backends.base import BackendError
from friday.skills.backends.browser import BrowserBackend
from friday.skills.manifest import Step
from friday.skills.validate import validate_manifest

from test_browser_backend import FakeBackendForBrowser, make_test_skill


@pytest.mark.parametrize(
    "url, site",
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtube.com/", "youtube"),
        ("https://m.youtube.com/feed", "youtube"),
        ("https://www.google.com/search?q=x", "google"),
        ("https://github.com/anthropics", "github"),
        ("https://en.wikipedia.org/wiki/AI", "wikipedia"),
        ("https://stackoverflow.com/questions", "stackoverflow"),
    ],
)
def test_site_for_url_known_hosts(url, site):
    assert site_for_url(url) == site


@pytest.mark.parametrize(
    "url",
    [
        "https://evilyoutube.com/",
        "https://youtube.com.evil.example/",
        "https://example.com/?u=youtube.com",
        "http://localhost/youtube.com",
        "chrome://newtab/",
        "about:blank",
        "",
        "javascript:alert(1)",
    ],
)
def test_site_for_url_rejects_lookalikes_and_non_web(url):
    assert site_for_url(url) is None


@pytest.fixture
def fake():
    f = FakeBackendForBrowser()
    set_backend(f)
    yield f
    reset_backend()


def test_search_here_on_youtube_uses_the_youtube_template(fake):
    fake.tabs = ["https://www.youtube.com/watch?v=abc"]
    out = b.search_here("artificial intelligence")
    assert fake.navigated == [
        "https://www.youtube.com/results?search_query=artificial%20intelligence"
    ]
    assert "youtube" in out


def test_search_here_refuses_an_unknown_site_without_navigating(fake):
    fake.tabs = ["https://example.com/"]
    with pytest.raises(BrowserError, match="not a searchable site"):
        b.search_here("artificial intelligence")
    assert fake.navigated == []


def test_search_here_new_tab_keeps_the_youtube_tab_and_opens_the_result_beside_it(fake):
    fake.tabs = ["https://www.youtube.com/watch?v=abc"]
    b.search_here("cats", new_tab=True)
    assert fake.navigated == []
    assert fake.tabs == [
        "https://www.youtube.com/watch?v=abc",
        "https://www.youtube.com/results?search_query=cats",
    ]


def test_search_here_new_tab_on_an_unknown_page_uses_google(fake):
    fake.tabs = ["https://example.com/"]
    b.search_here("cats", new_tab=True)
    assert fake.tabs[-1] == "https://www.google.com/search?q=cats"


def test_executor_new_tab_site_opens_a_fresh_tab(fake):
    from friday.executor import execute

    fake.tabs = ["https://example.com/"]
    result = execute("browser_search", {"site": "current_new_tab", "query": "cats"})
    assert result.ok and fake.navigated == []
    assert fake.tabs[-1] == "https://www.google.com/search?q=cats"


def test_search_here_refuses_an_empty_query(fake):
    fake.tabs = ["https://www.youtube.com/"]
    with pytest.raises(BrowserError):
        b.search_here("   ")
    assert fake.navigated == []


def test_backend_runs_search_here_verb(fake):
    fake.tabs = ["https://www.youtube.com/"]
    backend = BrowserBackend(make_test_skill())
    step = Step(id="s", backend="browser", argv=("search_here", "ai"), dry_run="")
    result = backend.run(step, {})
    assert result.ok and "youtube" in result.output


def test_backend_search_here_unknown_site_is_a_backend_error(fake):
    fake.tabs = ["https://example.com/"]
    backend = BrowserBackend(make_test_skill())
    step = Step(id="s", backend="browser", argv=("search_here", "ai"), dry_run="")
    with pytest.raises(BackendError, match="searchable"):
        backend.run(step, {})


def test_search_here_strips_trailing_stt_punctuation(fake):
    fake.tabs = ["https://www.youtube.com/"]
    b.search_here("artificial intelligence.")
    assert fake.navigated == [
        "https://www.youtube.com/results?search_query=artificial%20intelligence"
    ]


# --- browser_search with site="current" through the executor -------------------


def test_executor_current_site_searches_the_tab_site(fake):
    from friday.executor import execute

    fake.tabs = ["https://www.youtube.com/watch?v=abc"]
    result = execute("browser_search", {"site": "current", "query": "AI"})
    assert result.ok
    assert fake.navigated == ["https://www.youtube.com/results?search_query=AI"]


def test_executor_still_refuses_an_unknown_site(fake):
    from friday.executor import ExecutionError, execute

    with pytest.raises(ExecutionError):
        execute("browser_search", {"site": "myspace", "query": "AI"})


def test_executor_current_site_on_unknown_page_is_an_execution_error(fake):
    from friday.executor import ExecutionError, execute

    fake.tabs = ["https://example.com/"]
    with pytest.raises(ExecutionError, match="searchable"):
        execute("browser_search", {"site": "current", "query": "AI"})
    assert fake.navigated == []


# --- no debug port: read the frontmost browser tab over AppleScript ------------


def _fake_osascript(monkeypatch, frontmost, url=""):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        script = cmd[-1]
        out = frontmost if "System Events" in script else url
        return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")

    monkeypatch.setattr(b.subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize(
    "app, expect",
    [
        ("Brave Browser", 'tell application "Brave Browser" to return URL of active tab of front window'),
        ("Google Chrome", 'tell application "Google Chrome" to return URL of active tab of front window'),
        ("Safari", 'tell application "Safari" to return URL of front document'),
    ],
)
def test_front_browser_url_asks_the_frontmost_known_browser(monkeypatch, app, expect):
    calls = _fake_osascript(monkeypatch, app, "https://www.youtube.com/")
    assert b._front_browser_url() == "https://www.youtube.com/"
    assert calls[-1][-1] == expect


def test_front_browser_url_refuses_when_no_known_browser_is_frontmost(monkeypatch):
    calls = _fake_osascript(monkeypatch, "Finder")
    with pytest.raises(BrowserError, match="browser"):
        b._front_browser_url()
    assert len(calls) == 1  # never asked Finder for a URL


def test_search_here_falls_back_to_the_front_tab_when_no_debug_port(monkeypatch):
    class Unreachable:
        def get_current_url(self):
            raise b.BrowserUnreachable("no port")

        def navigate(self, url):
            raise b.BrowserUnreachable("no port")

    opened = []
    set_backend(Unreachable())
    monkeypatch.setattr(b, "_front_browser_url", lambda: "https://www.youtube.com/watch?v=1")
    monkeypatch.setattr(
        b.subprocess,
        "run",
        lambda cmd, **kw: opened.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    try:
        out = b.search_here("AI")
    finally:
        reset_backend()
    assert "youtube" in out
    assert opened == [["open", "https://www.youtube.com/results?search_query=AI"]]
