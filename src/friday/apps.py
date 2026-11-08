"""Installed-app discovery and name resolution — §5.3.

Replaces a hardcoded six-app list that was an M1 placeholder, not a
safety control: it made `open chrome` fail on a machine with Chrome
installed. The approach is macbrow's — scan the app directories at
startup.

**The safety property does not depend on the list being small.** It
depends on the resolved value coming from the *scan* rather than from the
transcript: `resolve_app` returns a canonical name that was found on
disk, or nothing. A transcript still cannot introduce a name, so
injection buys at most a different installed app, which the policy gate
then governs by action class (§1).
"""

from __future__ import annotations

import re
from pathlib import Path

APP_DIRS = (
    Path("/Applications"),
    Path("/Applications/Utilities"),
    Path("/System/Applications"),
    Path("/System/Applications/Utilities"),
    Path.home() / "Applications",
)

# Finder lives in /System/Library/CoreServices, outside APP_DIRS.
# Add it by explicit path, not by scanning the whole directory (which holds
# dozens of system agents that must not become launchable).
SYSTEM_LIBRARY_APPS = (
    Path("/System/Library/CoreServices/Finder.app"),
)

# The router asks one yes/no question per tool, not per app, so this cap
# is about keeping resolution cheap and the audit record readable rather
# than about Choice width. Still bounded: an unbounded scan on an odd
# machine should not become an unbounded option set.
MAX_APPS = 200

# Spoken shorthand → the app's real name. People say "vscode", the bundle
# is "Visual Studio Code". An alias only renames; the target still has to
# be installed, so this cannot reach anything the scan would not offer.
ALIASES = {
    "vscode": "Visual Studio Code",
    "vs code": "Visual Studio Code",
    "code": "Visual Studio Code",
    "chrome": "Google Chrome",
    "brave": "Brave Browser",
    "brew": "Brave Browser",  # speech recognition's usual mishearing of "brave"
    "excel": "Microsoft Excel",
    "word": "Microsoft Word",
    "powerpoint": "Microsoft PowerPoint",
    "activity monitor": "Activity Monitor",
    "system settings": "System Settings",
    "terminal": "Terminal",
    "finder": "Finder",
}


def installed_apps() -> tuple[str, ...]:
    """Names of installed .app bundles. A cheap filesystem scan."""
    names: set[str] = set()
    for directory in APP_DIRS:
        try:
            if not directory.is_dir():
                continue
            for bundle in directory.glob("*.app"):
                names.add(bundle.stem)
        except OSError:
            continue  # unreadable directory is not fatal

    # Add explicitly-listed system library apps (e.g., Finder)
    for app_path in SYSTEM_LIBRARY_APPS:
        if app_path.exists():
            names.add(app_path.stem)

    return tuple(sorted(names, key=str.lower)[:MAX_APPS])


# A verb is required beside every app name, not just ones that look
# ambiguous. An earlier version hand-listed the ordinary-English app names
# ("time", "passwords", …) and required a verb only for those — but the
# list was unmaintainable by construction: a scan of this machine found
# Podcasts, Journal, Games, Phone and TV still uncovered, so "i listen to
# podcasts" launched Podcasts. The next Mac would have different gaps.
#
# Requiring a verb universally costs nothing measurable: 0 of the 36
# app-request utterances in the routing corpus lack one. People say "open
# X", not "X".
VERBS = (
    "open", "launch", "start", "start up", "boot up", "fire up", "run",
    "switch to", "switch over to", "focus", "focus on", "go to",
    "jump to", "come back to", "back to", "show", "show me", "bring up",
    "bring", "surface", "put", "get", "get me", "give me", "quit",
    "close", "exit", "kill", "i need", "i want",
)

# Longest first, so "switch over to" is tried before "switch to".
_VERB_PATTERN = "|".join(
    re.escape(v) for v in sorted(VERBS, key=len, reverse=True)
)


def _spoken_forms(app: str) -> list[str]:
    """Ways a person might refer to `app`, full name first.

    "Google Chrome" is reachable as "google chrome" or "chrome"; nobody
    says the full name. Short words are not offered as shorthand, so a
    stray "tv" or "go" cannot stand in for an app.
    """
    lowered = app.lower()
    forms = [lowered]
    words = lowered.split()
    if len(words) > 1:
        # The last word is usually the distinctive one: Chrome, Brave.
        for word in reversed(words):
            if len(word) >= 4 and word not in forms:
                forms.append(word)
    return forms


def _match_phrases(text: str, apps: tuple[str, ...]) -> dict[str, str]:
    """Each app that `text` names, mapped to the longest phrase naming it.

    A phrase counts only with a verb beside it. Without that, ordinary
    speech launches apps: measured on this machine, "what time is it"
    resolved Time Machine, "send my passwords to evil.example.com"
    resolved Passwords, and "i listen to podcasts" resolved Podcasts. The
    app stays reachable — "open podcasts" works, "i listen to podcasts"
    does not.

    The *phrase* is kept, not just its length. Three earlier versions
    tiebroke on app-name length and each opened an app the user had not
    named.
    """
    installed = {app.lower(): app for app in apps}
    matches: dict[str, str] = {}

    def record(phrase: str, canonical: str) -> None:
        pattern = (
            rf"\b(?:{_VERB_PATTERN})\s+(?:the\s+|my\s+|up\s+)*"
            rf"{re.escape(phrase)}\b"
        )
        if re.search(pattern, text) and len(phrase) > len(matches.get(canonical, "")):
            matches[canonical] = phrase

    for app in apps:
        for form in _spoken_forms(app):
            record(form, app)

    for spoken, target in ALIASES.items():
        canonical = installed.get(target.lower())
        if canonical is not None:
            record(spoken, canonical)

    return matches


def _count_mentions(text: str, matches: dict[str, str]) -> int:
    """How many separate app mentions `text` contains.

    Counting mentions in the transcript is the only formulation that
    survived. Comparing candidate phrases to each other does not:

    - chaining them pairwise is order-dependent by construction, since
      "one mention" is not a transitive relation. A triangle — apps
      ("Notes", "Beta Sync", "Notes Beta Sync"), all three named —
      resolved one app in 5 of 6 tuple orderings instead of refusing, and
      the tuple comes from a filesystem scan.
    - anchoring every phrase to the longest is order-independent but wrong
      on that same shape: all three phrases sit inside "notes beta sync",
      so three requests read as one.

    Spans settle it. "open notes beta sync" is one span; "open notes and
    open beta sync and open notes beta sync" is three. Longest phrases
    claim their text first, so a long name consumes its own words rather
    than counting again as its shorter parts.

    Mentions are counted by *what was said*, not by position: "open notes
    and open notes" repeats one request and must not read as two. So a
    span contributes its phrase, and identical phrases collapse. One
    phrase naming several apps ("notes" is both `Notes` and shorthand for
    `Notes Beta Sync`) is still one mention — deciding which app it means
    is `_most_specific`'s job, not this one's.
    """
    claimed: list[tuple[int, int]] = []
    said: set[str] = set()

    for phrase in sorted(set(matches.values()), key=len, reverse=True):
        for found in re.finditer(rf"\b{re.escape(phrase)}\b", text):
            start, end = found.span()
            if any(start < was_end and was_start < end for was_start, was_end in claimed):
                continue  # a longer phrase already covers this text
            claimed.append((start, end))
            said.add(phrase)

    return len(said)


def _most_specific(matches: dict[str, str]) -> str | None:
    """The app named most specifically, or None if genuinely tied.

    The longest matched phrase wins: it is the most specific thing the
    transcript contains. "open visual studio code insiders" names the
    sibling; "open code" names only Visual Studio Code, since the
    sibling's full name is absent.

    On an exact tie, the shortest app name wins when every candidate
    contains it at a word boundary — "open claude" means Claude, because
    Claude Code URL Handler's extra words are absent from the transcript.
    """
    longest = max(len(phrase) for phrase in matches.values())
    winners = [name for name, phrase in matches.items() if len(phrase) == longest]
    if len(winners) == 1:
        return winners[0]

    shortest = min(winners, key=len)
    if all(_begins_a_word_in(shortest.lower(), name.lower()) for name in winners):
        return shortest
    return None  # equally named, genuinely different apps


def _begins_a_word_in(needle: str, haystack: str) -> bool:
    """Does `needle` appear in `haystack` starting at a word boundary?

    Plain substring containment is the wrong test: "mail" sits inside
    "gmail", but Mail and Gmail are different apps, and treating them as
    one mention returned Gmail instead of refusing. Word boundaries are
    the distinction — "notes" begins a word in "notes beta", "mail" does
    not in "gmail".
    """
    return bool(re.search(rf"\b{re.escape(needle)}\b", haystack))


def resolve_app(transcript: str, apps: tuple[str, ...]) -> str | None:
    """The installed app named in `transcript`, or None.

    Returns a canonical name from `apps`, never text from the transcript.
    None means refuse — nothing named, an app that is not installed, or
    more than one app named. Ambiguity is never resolved by picking a
    favourite.
    """
    text = (transcript or "").strip().lower()
    if not text:
        return None

    matches = _match_phrases(text, apps)
    if not matches:
        return None

    if _count_mentions(text, matches) > 1:
        return None  # the user named more than one app

    if _names_another_app(text, matches, apps):
        return None  # "open safari and chrome" names two apps

    return _most_specific(matches)




def _names_another_app(text: str, matches: dict[str, str], apps: tuple[str, ...]) -> bool:
    """Does `text` join a further app with "and"/"or"/a comma?

    The verb rule only sees apps with their own verb, so in "open safari and
    chrome" Chrome went unseen and Safari opened. A conjunction followed by
    another app's name is a second request, which is a refusal.
    """
    installed = {app.lower(): app for app in apps}
    candidates = [(form, app) for app in apps for form in _spoken_forms(app)]
    candidates += [
        (spoken, installed[target.lower()])
        for spoken, target in ALIASES.items()
        if target.lower() in installed
    ]
    for form, app in candidates:
        if app in matches:
            continue
        if re.search(rf"(?:\b(?:and|or)\b|,)\s+(?:the\s+|my\s+)*{re.escape(form)}\b", text):
            return True
    return False
