"""The tool registry — ARCHITECTURE.md §5.2, §5.6 (R3).

This module defines the entire space of actions the agent can take.
Every entry is a named function with typed, constrained arguments. There
is deliberately no general-purpose primitive: no shell, no arbitrary
AppleScript, no fetch-any-URL.

Tool definitions live in tools.json (macbrow's pattern) rather than a
Python literal — this module loads and validates that file into the
same `Tool`/`REGISTRY` shape every consumer (agent.py, router.py,
slots.py, executor.py) already expects, so widening the tool set means
editing tools.json plus adding any script/binary/module a new argument
type needs, not touching this file. The validation below exists so a
mistake in tools.json fails at import time, not as a runtime surprise.

Argument kinds:
  enum          - one of a fixed set of values (the model picks an index)
  installed_app - resolved against a filesystem scan (apps.py)
  url_shortcut  - resolved against a closed table (shortcuts.py)
  literal_span  - a verbatim span of the user's transcript (§5.3)
  control_index - an index into browser.py's freshly fetched control table
  skill_ref     - the id of an enabled, pinned skill (skills/store.py)
  skill_slots   - that skill's slot values, filled by skills/matcher.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from friday.policy import ActionClass

TOOLS_PATH = Path(__file__).parent / "tools.json"

_ARGUMENT_TYPES = {
    "enum", "installed_app", "url_shortcut", "literal_span",
    "control_index", "skill_ref", "skill_slots",
}


# Apps come from a filesystem scan, not a hardcoded list — the six-app
# version was an M1 placeholder that made `open chrome` fail on a machine
# with Chrome installed. The guarantee is unchanged: a resolved app name
# comes from the scan, never from the transcript, so a transcript cannot
# name something that is not installed (see apps.py).
#
# Cached: scanned once per process, because an app appearing mid-session
# is far rarer than an utterance, and a scan per utterance would be waste
# on the hot path.
@lru_cache(maxsize=1)
def known_apps() -> tuple[str, ...]:
    from friday.apps import installed_apps

    return installed_apps()


@dataclass(frozen=True)
class Tool:
    name: str
    # Phrased as a yes/no question about the *user's* request: the
    # router asks one noul per tool rather than one wide choice, so this
    # string is the question laya answers. Describing what the tool does
    # ("launches an app") measured worse than asking what the user wants
    # ("Is the user asking to open an app?") — see docs/ROUTING.md.
    description: str
    action_class: ActionClass
    arguments: Mapping[str, dict] = field(default_factory=dict)
    script: str | None = None  # allow-listed .applescript, if any
    binary: str | None = None  # allow-listed system binary, if any
    category: str | None = None  # grouping only; never affects policy


# Public (not `_`-prefixed): executor.py imports this too, so the two
# allow-lists can't drift apart. Deliberately the same narrow-allow-list
# pattern as the .applescript files in scripts/ — widening it is as
# reviewed an act as adding a new script.
ALLOWED_BINARIES = {"screencapture"}


def _load_tool(entry: dict) -> Tool:
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"tools.json entry has no valid name: {entry!r}")

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        # Not cosmetic: the router asks one noul per tool using exactly
        # this string (docs/ROUTING.md), so a blank one is silently
        # unroutable rather than a loud failure.
        raise ValueError(f"{name}: description must be a non-blank string")

    try:
        action_class = ActionClass(entry["action_class"])
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"{name}: action_class {entry.get('action_class')!r} is not a "
            f"known ActionClass"
        ) from exc

    arguments = entry.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError(f"{name}: arguments must be an object")
    for arg_name, spec in arguments.items():
        if not isinstance(spec, dict) or spec.get("type") not in _ARGUMENT_TYPES:
            raise ValueError(
                f"{name}.{arg_name}: unknown argument type {spec!r}"
            )
        if spec["type"] == "enum":
            values = spec.get("values")
            # Caught here, not at slots.py/executor.py, so a malformed
            # enum fails at load time with a ValueError rather than
            # surfacing as a TypeError (missing/null/[]) or, worse,
            # silently misbehaving: a code-reviewer pass caught that a
            # bare `not values` check let a truthy non-list (a string or
            # dict) through, where slots.py's `tuple(values)` would
            # iterate its characters/keys instead of raising, and
            # executor.py's `value not in values` would become substring
            # containment instead of an allow-list check — both wrong
            # silently rather than loud, which is worse than either.
            if not isinstance(values, list) or not values:
                raise ValueError(
                    f"{name}.{arg_name}: enum requires a non-empty list "
                    f"for 'values', got {values!r}"
                )

    script = entry.get("script")
    if script is not None and (not isinstance(script, str) or not script.strip()):
        raise ValueError(f"{name}: script must be a non-blank string or absent")

    binary = entry.get("binary")
    if script and binary:
        raise ValueError(f"{name}: declares both script and binary")
    if binary is not None and binary not in ALLOWED_BINARIES:
        raise ValueError(
            f"{name}: binary {binary!r} is not in the allow-list "
            f"{sorted(ALLOWED_BINARIES)}"
        )

    category = entry.get("category")
    if category is not None and (not isinstance(category, str) or not category.strip()):
        raise ValueError(f"{name}: category must be a non-blank string or absent")

    return Tool(
        name=name,
        description=description,
        action_class=action_class,
        arguments=arguments,
        script=script,
        binary=binary,
        category=category,
    )


def _load_registry(path: Path) -> Mapping[str, Tool]:
    entries = json.loads(path.read_text())
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected a JSON array of tool entries")

    tools = [_load_tool(entry) for entry in entries]

    names = [t.name for t in tools]
    if len(names) != len(set(names)):
        raise ValueError(f"{path}: duplicate tool name in {sorted(names)}")

    return MappingProxyType({t.name: t for t in tools})


REGISTRY: Mapping[str, Tool] = _load_registry(TOOLS_PATH)
