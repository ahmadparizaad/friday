"""The skill matcher — docs/PLAN-SKILLS.md (S3, T6).

Deterministic, no model. Each skill declares trigger patterns with
`{slot}` placeholders; the matcher tries each enabled skill's triggers
in order and the first whose pattern matches AND whose slots all fill
wins. A slot fills from its regex group, its enum default, or a
resolver (file, app, contact). A slot that cannot fill fails that trigger;
a skill whose triggers all fail is not matched; nothing matches → the
caller refuses (today) or hands off to the planner (later).

The matcher never guesses: an ambiguous file, an unknown app, or a
missing slot is a refusal, not a favourite.
"""

from __future__ import annotations

import re
from typing import Mapping

from friday.skills.manifest import Skill
from friday.skills.resolvers import (
    AppResolver,
    ContactResolver,
    DateResolver,
    FileResolver,
    ResolverError,
)


class MatchError(Exception):
    """No skill matched. The caller must refuse, not substitute."""


class Resolvers:
    """The resolver bundle. Injectable for tests."""

    def __init__(self, file=None, app=None, date=None, contact=None):
        self.file = file or FileResolver(
            ("~/Downloads", "~/Documents", "~/Desktop")
        )
        self.app = app or AppResolver(_installed_apps())
        self.date = date or DateResolver()
        self.contact = contact or ContactResolver()


def _installed_apps() -> tuple[str, ...]:
    from friday.registry import known_apps

    return known_apps()


def _compile(trigger: str) -> re.Pattern:
    """Compile a trigger pattern, turning {slot} into a named group.

    The trigger is already a regex (the manifest author writes regex,
    e.g. `(?:the )?`); only the {slot} placeholders are substituted, so
    the author's regex syntax is preserved.
    """
    parts: list[str] = []
    last = 0
    for m in re.finditer(r"\{(\w+)\}", trigger):
        parts.append(trigger[last : m.start()])
        parts.append(f"(?P<{m.group(1)}>.+)")
        last = m.end()
    parts.append(trigger[last:])
    return re.compile("".join(parts), re.IGNORECASE)


def _canonical(value: str, values: tuple[str, ...]) -> str:
    for v in values:
        if v.lower() == value.lower():
            return v
    return value


def _fill(
    skill: Skill,
    name: str,
    captured: str | None,
    utterance: str,
    resolvers: Resolvers,
) -> str | None:
    """Fill one slot, or return None if this trigger cannot supply it."""
    slot = skill.slots[name]
    if captured is not None and captured.strip():
        value = captured.strip()
        if slot.type in ("file", "app", "contact"):
            # speech-to-text ends sentences with "."; it is not part of a name
            value = value.rstrip(".!?,;: ")
        if slot.type == "enum":
            if value.lower() in [v.lower() for v in slot.values]:
                return _canonical(value, slot.values)
            return None
        if slot.type == "file":
            try:
                return resolvers.file.resolve(value)
            except ResolverError:
                return None
        if slot.type == "app":
            try:
                return resolvers.app.resolve(value)
            except ResolverError:
                return None
        if slot.type == "contact":
            # a name becomes exactly one address, or the trigger fails
            try:
                return resolvers.contact.resolve(value)
            except ResolverError:
                return None
        return value  # text, duration
    # Not captured by the pattern: an optional slot takes its default
    # (the manifest only allows defaults on text, enum and duration).
    if slot.default is not None:
        return slot.default
    return None


def match(
    utterance: str,
    skills: tuple[Skill, ...],
    resolvers: Resolvers | None = None,
) -> tuple[str, dict[str, str]]:
    """Return (skill_id, slots) for the first matching skill.

    Raises MatchError when nothing matches or a required slot cannot
    fill. Never guesses.
    """
    if not utterance or not utterance.strip():
        raise MatchError("empty utterance")
    resolvers = resolvers or Resolvers()
    text = utterance.strip()

    for skill in skills:
        for trigger in skill.triggers:
            pattern = _compile(trigger)
            m = pattern.search(text)
            if m is None:
                continue
            slots: dict[str, str] = {}
            ok = True
            for name in skill.slots:
                captured = m.groupdict().get(name)
                value = _fill(skill, name, captured, text, resolvers)
                if value is None:
                    ok = False
                    break
                slots[name] = value
            if ok:
                return skill.id, slots
    raise MatchError("no skill matched the utterance")
