"""URL shortcut resolution — the closed set behind `open_url` (§1).

A URL reaches the network, so it cannot be a free-spoken literal span:
there is nothing to validate a spoken domain against. Instead, following
the same pattern as apps.py's ALIASES, a spoken shortcut word resolves
against a small, code-reviewed table to a fixed url, and a spoken browser
word (or none, meaning the default browser) resolves against a second
fixed table. Words are matched like enums; the url and browser are
literal values from these tables, never generated and never taken from
the transcript.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# word -> url. The url is exactly what gets opened — adding an entry here
# is a reviewed, deliberate act, the same as adding a tool to the registry.
SHORTCUTS: dict[str, str] = {
    "email": "mail.google.com",
    "youtube": "www.youtube.com",
    "github": "github.com",
    "wikipedia": "www.wikipedia.org",
    # common services, opened on the web when no native app is wanted.
    "whatsapp": "web.whatsapp.com",
    "spotify": "open.spotify.com",
    "google maps": "maps.google.com",
    "netflix": "www.netflix.com",
    "chat gpt": "chatgpt.com",
    "youtube music": "music.youtube.com",
}

# Spoken browser word -> app name. Naming none means the default browser,
# passed to the script as the literal DEFAULT_BROWSER (not an app name).
BROWSERS: dict[str, str] = {
    "chrome": "Google Chrome",
    "brave": "Brave Browser",
    "brew": "Brave Browser",  # speech recognition's usual mishearing of "brave"
}
DEFAULT_BROWSER = "default"


# --- user services and web wording -----------------------------------------------

USER_SERVICES_PATH = Path.home() / ".config/friday-local/services.json"

# A bare public hostname: labels of letters/digits/hyphens, at least one dot,
# no scheme, path, port, space or quote. open_url.applescript prepends
# https:// itself, so anything else could break out of that template.
_SAFE_HOST = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)
_SAFE_WORD = re.compile(r"[a-z0-9]+(?: [a-z0-9]+)*")
_WEB_WORDING = re.compile(
    r"\b(?:on the web|website|web app|web version|in (?:the|a) browser)\b"
)


def _user_services() -> dict[str, str]:
    """Valid entries from the user's services file; anything else is dropped."""
    try:
        data = json.loads(USER_SERVICES_PATH.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        word.strip().lower(): host.strip().lower()
        for word, host in data.items()
        if isinstance(word, str)
        and isinstance(host, str)
        and _SAFE_WORD.fullmatch(word.strip().lower())
        and _SAFE_HOST.fullmatch(host.strip().lower())
    }


def _table() -> dict[str, str]:
    return {**_user_services(), **SHORTCUTS}  # built-ins win


def _named(word: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def wants_web(transcript: str) -> bool:
    """True when the user named a browser or asked for the web version."""
    text = transcript.strip().lower()
    return any(_named(w, text) for w in BROWSERS) or bool(_WEB_WORDING.search(text))




class ShortcutError(Exception):
    """More than one shortcut word was named. Ambiguity is not resolved
    by picking a favourite, mirroring slots._match_enum."""


def resolve_shortcut(transcript: str) -> tuple[str, str, str] | None:
    """Return (word, url, browser) for the shortcut named in transcript,
    or None if none is named. Never guesses a default.

    Raises ShortcutError if more than one shortcut word is named — with
    a single table entry this cannot yet happen, but the check is here
    so a second entry doesn't silently introduce the ambiguity that
    every other slot type already refuses on.
    """
    text = transcript.strip().lower()
    if not text:
        return None
    found = [(word, url) for word, url in _table().items() if _named(word, text)]
    # a longer name wins over a shorter one it contains
    # ("youtube music" is not also "youtube").
    found = [
        (word, url)
        for word, url in found
        if not any(other != word and _named(word, other) for other, _ in found)
    ]
    if len(found) > 1:
        raise ShortcutError(
            f"ambiguous: transcript names {sorted(w for w, _ in found)}"
        )
    if not found:
        return None
    browsers = {
        app for word, app in BROWSERS.items() if re.search(rf"\b{re.escape(word)}\b", text)
    }
    if len(browsers) > 1:
        raise ShortcutError(f"ambiguous: transcript names browsers {sorted(browsers)}")
    word, url = found[0]
    return word, url, browsers.pop() if browsers else DEFAULT_BROWSER
