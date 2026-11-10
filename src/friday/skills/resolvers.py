"""Slot resolvers — docs/PLAN-SKILLS.md (S4).

A resolver turns a span of the utterance into a value the machine can
vouch for: a file that exists (Spotlight), an app that is installed,
a date. A resolver never invents a value — zero matches and ambiguous
matches both raise, so the caller refuses rather than guessing.

Resolvers are injectable so tests can run without Spotlight or a real
app scan.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Protocol


class ResolverError(Exception):
    """The value could not be resolved. The caller must refuse."""


class Resolver(Protocol):
    def resolve(self, text: str) -> str:
        """Return the resolved value, or raise ResolverError."""
        ...


class FileResolver:
    """Resolves a file by name against Spotlight, within allowed dirs.

    Returns the single match. Zero matches and more than one match both
    raise: ambiguity is surfaced to the user as a short question, never
    resolved by picking a favourite.
    """

    def __init__(self, allowed_dirs: tuple[str, ...], binary: str = "mdfind"):
        self._allowed_dirs = allowed_dirs
        self._binary = binary

    def resolve(self, text: str) -> str:
        if not text.strip():
            raise ResolverError("no file name given")
        parts = [self._binary]
        for directory in self._allowed_dirs:
            parts += ["-onlyin", str(Path(directory).expanduser())]
        parts += ["-name", text]
        try:
            proc = subprocess.run(
                parts,
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise ResolverError(f"file search failed: {exc}") from exc
        matches = [
            line.strip()
            for line in proc.stdout.splitlines()
            if line.strip()
        ]
        if not matches:
            raise ResolverError(f"no file named {text!r} found")
        # confine to allowed_dirs after resolving symlinks, so a
        # link inside an allowed dir cannot point the skill elsewhere.
        roots = [Path(d).expanduser().resolve() for d in self._allowed_dirs]
        resolved = [Path(m).resolve() for m in matches]
        inside = [r for r in resolved if any(r.is_relative_to(root) for root in roots)]
        if not inside:
            raise ResolverError(
                f"{text!r} only matches files outside the allowed directories"
            )
        if len(inside) > 1:
            raise ResolverError(
                f"ambiguous: {text!r} matches {len(inside)} files"
            )
        return str(inside[0])


class AppResolver:
    """Resolves an app name against the installed-app scan."""

    def __init__(self, apps: tuple[str, ...]):
        self._apps = apps

    def resolve(self, text: str) -> str:
        """A slot holds a bare name ("Safari"), not a request ("open
        Safari"), so it is compared whole against each app's spoken
        forms. Unknown, ambiguous ("chrome" with two Chromes) or several
        names all refuse."""
        from friday.apps import ALIASES, _spoken_forms

        name = re.sub(r"^(?:the|my)\s+", "", text.strip().lower())
        found = {app for app in self._apps if name and name in _spoken_forms(app)}
        alias = ALIASES.get(name)
        if alias is not None:
            found |= {app for app in self._apps if app.lower() == alias.lower()}
        if len(found) != 1:
            kind = "ambiguous app name" if found else "no installed application named"
            raise ResolverError(f"{kind} {text!r}")
        return found.pop()


class DateResolver:
    """Resolves a date span to ISO format.

    Deliberately small: today, tomorrow, yesterday, and ISO dates. A
    relative date the resolver does not understand raises rather than
    being guessed.
    """

    def __init__(self, today=None):
        from datetime import date

        self._today = today or date.today()

    def resolve(self, text: str) -> str:
        from datetime import date, timedelta

        word = text.strip().lower()
        if word in ("today", "tonight"):
            return self._today.isoformat()
        if word == "tomorrow":
            return (self._today + timedelta(days=1)).isoformat()
        if word == "yesterday":
            return (self._today - timedelta(days=1)).isoformat()
        try:
            return date.fromisoformat(word).isoformat()
        except ValueError as exc:
            raise ResolverError(f"cannot resolve date {text!r}") from exc



# --- contacts --------------------------------------------------------------------

_CONTACT_SCRIPT = Path(__file__).parent / "find_contact.applescript"
_EMAIL_RE = re.compile(r"[^@\s-][^@\s]*@[^@\s]+\.[^@\s]+")
_UNIT_SEP = "\x1f"  # names may contain tabs; the unit separator cannot be typed


def _contacts_lookup(name: str) -> list[tuple[str, str]]:
    """(name, email) rows from Contacts whose name contains `name`.

    Read-only AppleScript; rows with a malformed address are dropped.
    """
    proc = subprocess.run(
        ["osascript", str(_CONTACT_SCRIPT), name],
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
    )
    rows = []
    for line in proc.stdout.splitlines():
        person, sep, email = line.partition(_UNIT_SEP)
        email = email.strip()
        if sep and person.strip() and _EMAIL_RE.fullmatch(email):
            rows.append((person, email))
    return rows


class ContactResolver:
    """Resolves a name to exactly one email address via Contacts.

    Matches whole words of a contact's name ("ann" is Ann Lee, never
    Joanna); an exact full name beats partial matches. More than one
    person, or one person with several addresses, raises: the user is
    asked, a favourite is never picked.
    """

    def __init__(self, lookup=_contacts_lookup):
        self._lookup = lookup

    def resolve(self, text: str) -> str:
        query = text.strip()
        if not query:
            raise ResolverError("no contact name given")
        if _EMAIL_RE.fullmatch(query):
            return query
        try:
            rows = self._lookup(query)
        except subprocess.TimeoutExpired as exc:
            raise ResolverError(
                "contacts lookup timed out; allow Automation access to Contacts"
            ) from exc
        except OSError as exc:
            raise ResolverError(f"contacts lookup failed: {exc}") from exc

        q = query.lower()
        words = q.split()
        exact = [r for r in rows if r[0].strip().lower() == q]
        partial = [
            r for r in rows if all(w in r[0].lower().split() for w in words)
        ]
        candidates = exact or partial
        if not candidates:
            hint = sorted({r[0] for r in rows})
            suffix = f" (did you mean {', '.join(hint)}?)" if hint else ""
            raise ResolverError(f"no contact named {query!r}{suffix}")
        names = sorted({r[0] for r in candidates})
        if len(names) > 1:
            raise ResolverError(f"ambiguous: {query!r} matches {', '.join(names)}")
        if len(candidates) > 1:
            raise ResolverError(f"ambiguous: {names[0]} has more than one address")
        return candidates[0][1]
