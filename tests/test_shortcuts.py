"""URL shortcut resolution tests.

A spoken site word resolves against a small, code-reviewed table to a
fixed URL; a spoken browser word (chrome / brave) resolves against a
second fixed table, and no browser word means the default browser. Neither
value is ever generated or taken from the transcript — same tier as
profile.toml (§1). Both are matched like enums.
"""

import pytest

import friday.shortcuts as shortcuts_mod
from friday.shortcuts import (
    BROWSERS,
    DEFAULT_BROWSER,
    SHORTCUTS,
    ShortcutError,
    resolve_shortcut,
)


def test_no_browser_named_means_the_default_browser():
    assert resolve_shortcut("open my email") == ("email", "mail.google.com", DEFAULT_BROWSER)
    assert resolve_shortcut("open youtube in the browser") == (
        "youtube", "www.youtube.com", DEFAULT_BROWSER,
    )


def test_chrome_and_brave_are_honoured():
    assert resolve_shortcut("open youtube in chrome") == (
        "youtube", "www.youtube.com", "Google Chrome",
    )
    assert resolve_shortcut("Open YouTube in Google Chrome.") == (
        "youtube", "www.youtube.com", "Google Chrome",
    )
    assert resolve_shortcut("open my email in brave") == (
        "email", "mail.google.com", "Brave Browser",
    )


def test_misheard_brave_is_honoured_not_silently_the_default_browser():
    assert resolve_shortcut("Open YouTube in brew.") == (
        "youtube", "www.youtube.com", "Brave Browser",
    )


def test_brave_and_its_mishearing_together_are_one_browser_not_ambiguous():
    assert resolve_shortcut("open youtube in brave brew")[2] == "Brave Browser"


def test_case_insensitive():
    assert resolve_shortcut("OPEN EMAIL") == ("email", "mail.google.com", DEFAULT_BROWSER)


def test_refuses_two_browsers():
    with pytest.raises(ShortcutError):
        resolve_shortcut("open youtube in chrome and brave")


def test_refuses_unknown_shortcut():
    assert resolve_shortcut("open my bank account") is None


def test_refuses_empty_transcript():
    assert resolve_shortcut("") is None


def test_every_shortcut_has_a_url():
    for word, url in SHORTCUTS.items():
        assert word, "shortcut key must not be blank"
        assert url.strip(), (word, "url")


def test_browser_table_is_reviewed_and_default_is_not_an_app_name():
    assert BROWSERS == {
        "chrome": "Google Chrome",
        "brave": "Brave Browser",
        "brew": "Brave Browser",  # speech-recognition mishearing, seen 3x in the audit log
    }
    assert DEFAULT_BROWSER not in BROWSERS.values()


def test_site_shortcuts_are_present():
    assert SHORTCUTS["email"] == "mail.google.com"
    assert SHORTCUTS["youtube"] == "www.youtube.com"
    assert SHORTCUTS["github"] == "github.com"
    assert SHORTCUTS["wikipedia"] == "www.wikipedia.org"


def test_every_shortcut_url_is_a_bare_host_no_scheme_or_path_injection():
    """open_url.applescript prepends https:// itself, so a table entry
    carrying a scheme, space or quote would break out of that template."""
    for word, url in SHORTCUTS.items():
        assert "://" not in url, word
        assert not any(c in url for c in " \"'\\"), word


def test_refuses_ambiguous_multiple_shortcuts(monkeypatch):
    """Two candidates is not a coin flip — mirrors slots._match_enum's
    ambiguity refusal."""
    monkeypatch.setitem(shortcuts_mod.SHORTCUTS, "bank", "bank.example.com")
    with pytest.raises(ShortcutError):
        resolve_shortcut("open my email and my bank")


# --- web services table (native app first, web fallback) ----------------------

@pytest.mark.parametrize(
    "utterance, word, url",
    [
        ("open whatsapp", "whatsapp", "web.whatsapp.com"),
        ("open spotify", "spotify", "open.spotify.com"),
        ("open google maps", "google maps", "maps.google.com"),
        ("open netflix in chrome", "netflix", "www.netflix.com"),
        ("open chat gpt", "chat gpt", "chatgpt.com"),
    ],
)
def test_common_services_resolve_to_their_web_host(utterance, word, url):
    assert resolve_shortcut(utterance)[:2] == (word, url)


def test_a_longer_service_name_wins_over_the_shorter_one_it_contains():
    assert resolve_shortcut("open youtube music")[:2] == ("youtube music", "music.youtube.com")
    assert resolve_shortcut("open youtube")[:2] == ("youtube", "www.youtube.com")


def test_wants_web_is_true_for_a_named_browser_or_web_wording():
    from friday.shortcuts import wants_web

    assert wants_web("open spotify in chrome")
    assert wants_web("open spotify on the web")
    assert wants_web("open the spotify website")
    assert not wants_web("open spotify")
    assert not wants_web("open spotify for me")


# --- user-added services (~/.config/friday-local/services.json) ---------------

@pytest.fixture
def services_file(tmp_path, monkeypatch):
    path = tmp_path / "services.json"
    monkeypatch.setattr(shortcuts_mod, "USER_SERVICES_PATH", path)
    return path


def test_user_service_resolves_like_a_built_in_one(services_file):
    services_file.write_text('{"my wiki": "wiki.example.com"}')
    assert resolve_shortcut("open my wiki")[:2] == ("my wiki", "wiki.example.com")


def test_user_services_cannot_override_a_built_in_entry(services_file):
    services_file.write_text('{"youtube": "evil.example.com"}')
    assert resolve_shortcut("open youtube")[1] == "www.youtube.com"


@pytest.mark.parametrize(
    "bad_host",
    ["https://evil.example.com", "evil.example.com/path", "evil example.com",
     'evil".example.com', "localhost", "", "a;b.com", 42],
)
def test_user_service_with_an_unsafe_host_is_ignored(services_file, bad_host):
    import json

    services_file.write_text(json.dumps({"sketchy": bad_host, "fine": "ok.example.com"}))
    assert resolve_shortcut("open sketchy") is None
    assert resolve_shortcut("open fine")[1] == "ok.example.com"


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '"text"', ""])
def test_unreadable_services_file_degrades_to_built_ins(services_file, content):
    services_file.write_text(content)
    assert resolve_shortcut("open youtube")[1] == "www.youtube.com"


def test_missing_services_file_is_fine(services_file):
    assert resolve_shortcut("open youtube")[1] == "www.youtube.com"
