"""Static safety checks for skills — docs/PLAN-SKILLS.md (S1, T2).

The validator is the source of truth for whether a skill is safe to
enable, not the prompt that drafted it. It is static: no model, no
execution, no network. Errors are stable codes so an authoring LLM (or
a human) can fix them in a loop.

Two entry points:

  validate_manifest(skill)      — checks the manifest's structure and
                                  safety declarations.
  validate_slots(skill, slots)  — checks concrete slot values against
                                  their declarations (run before steps).

A generated skill cannot add a shell call, raw network access, or a
path outside its allowed directories without the validator rejecting it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from friday.policy import ActionClass
from friday.skills.manifest import PROVENANCE, SLOT_TYPES, Skill

# Stable error codes. These are the contract authors fix against; do not
# renumber or rename without updating docs/PLAN-SKILLS.md.
SLOT_NO_PROVENANCE = "SLOT_NO_PROVENANCE"
ACTION_CLASS_TOO_LOW = "ACTION_CLASS_TOO_LOW"
SHELL_STRING_FORBIDDEN = "SHELL_STRING_FORBIDDEN"
PATH_OUTSIDE_ALLOWED_DIRS = "PATH_OUTSIDE_ALLOWED_DIRS"
MISSING_POSTCONDITION = "MISSING_POSTCONDITION"
MISSING_UNDO_OR_CONFIRM = "MISSING_UNDO_OR_CONFIRM"
BINARY_NOT_ALLOWED = "BINARY_NOT_ALLOWED"
DASH_LEADING_SLOT = "DASH_LEADING_SLOT"
ENUM_VALUE_NOT_ALLOWED = "ENUM_VALUE_NOT_ALLOWED"
SLOT_TOO_LONG = "SLOT_TOO_LONG"
# an applescript step's script must be a plain .applescript file
# inside the skill folder, outside the unpinned places (tests/, caches).
SCRIPT_NOT_ALLOWED = "SCRIPT_NOT_ALLOWED"

# The allow-listed CLI binaries (docs/PLAN-SKILLS.md backend order).
# Widening this is as reviewed an act as adding a new .applescript.
# `osascript` and `defaults` are deliberately absent: a slot fed to
# `osascript -e` is arbitrary AppleScript, and `defaults` lets a slot
# pick the domain. Scripts go through the applescript backend instead.
SKILL_BINARIES = frozenset(
    {
        "mdfind",
        "sips",
        "ffmpeg",
        "ffprobe",
        "qpdf",
        "pdfunite",
        "zip",
        "unzip",
        "open",
        "screencapture",
        "shortcuts",
    }
)

# Binaries whose presence in a step implies a destructive class.
_DESTRUCTIVE_BINARIES = frozenset({"rm", "trash"})

# Shell metacharacters that must never appear in an argv element: there
# is no shell, so their presence means a string-built command.
_SHELL_METACHARS = frozenset(";$()`|&<>\n\r")

_CLASS_RANK = {
    ActionClass.READ: 0,
    ActionClass.NAVIGATE: 1,
    ActionClass.MUTATE_LOCAL: 2,
    ActionClass.MUTATE_REMOTE: 3,
    ActionClass.DESTRUCTIVE: 4,
    ActionClass.BLOCKED: 5,
}


# Classes the policy gate always confirms (policy._NEVER_ALLOW), so a skill
# in one of them is gated even without an undo declaration.
_ALWAYS_CONFIRMS = frozenset(
    {ActionClass.MUTATE_LOCAL, ActionClass.MUTATE_REMOTE, ActionClass.DESTRUCTIVE}
)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# Folders whose files are excluded from the store's pin (store.skill_digest).
_UNPINNED_DIRS = frozenset({"tests", "__pycache__"})


def _placeholder(element: str) -> str | None:
    """The slot name if `element` is exactly one placeholder, else None."""
    m = _PLACEHOLDER.fullmatch(element)
    return m.group(1) if m else None


def _is_literal_path(element: str) -> bool:
    return element.startswith(("/", "~"))


def _script_allowed(script: str | None) -> bool:
    if not script:
        return False
    path = Path(script)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and path.suffix == ".applescript"
        and not _UNPINNED_DIRS.intersection(path.parts[:-1])
    )


def _within_allowed_dirs(element: str, allowed_dirs) -> bool:
    target = Path(element).expanduser().resolve()
    for allowed in allowed_dirs:
        try:
            target.relative_to(Path(allowed).expanduser().resolve())
            return True
        except ValueError:
            continue
    return False


def validate_manifest(skill: Skill) -> list[str]:
    """Return the list of error codes for a manifest. Empty means valid."""
    errors: list[str] = []

    for name, slot in skill.slots.items():
        if slot.provenance not in PROVENANCE:
            errors.append(SLOT_NO_PROVENANCE)
        if slot.type not in SLOT_TYPES:
            errors.append(SLOT_NO_PROVENANCE)

    if not skill.postcondition.type.strip() or not skill.postcondition.value.strip():
        errors.append(MISSING_POSTCONDITION)

    if (
        not skill.undo or not skill.undo.strip()
    ) and skill.action_class not in _ALWAYS_CONFIRMS:
        # No undo declared and no forced confirm: the skill could mutate
        # with no way back and no gate. `undo: none` is a declared "no
        # undo, force confirm" and is acceptable for any class.
        errors.append(MISSING_UNDO_OR_CONFIRM)

    highest = ActionClass.READ
    for step in skill.steps:
        implied = ActionClass.READ
        if step.action_class is not None:
            implied = step.action_class
        if step.delete:
            implied = ActionClass.DESTRUCTIVE
        if step.backend == "cli" and step.argv:
            binary = step.argv[0]
            if binary in _DESTRUCTIVE_BINARIES:
                implied = ActionClass.DESTRUCTIVE
            if binary not in SKILL_BINARIES:
                errors.append(BINARY_NOT_ALLOWED)
        if step.backend == "applescript" and not _script_allowed(step.script):
            errors.append(SCRIPT_NOT_ALLOWED)
        if _CLASS_RANK[implied] > _CLASS_RANK[highest]:
            highest = implied
        for element in step.argv:
            if any(ch in element for ch in _SHELL_METACHARS):
                errors.append(SHELL_STRING_FORBIDDEN)
            if "{" in element and _placeholder(element) is None:
                # A placeholder embedded in a larger string would
                # concatenate the value into a command — slot values must
                # occupy whole argv elements.
                errors.append(SHELL_STRING_FORBIDDEN)
            if (
                _is_literal_path(element)
                and skill.allowed_dirs
                and not _within_allowed_dirs(element, skill.allowed_dirs)
            ):
                errors.append(PATH_OUTSIDE_ALLOWED_DIRS)

    if _CLASS_RANK[skill.action_class] < _CLASS_RANK[highest]:
        errors.append(ACTION_CLASS_TOO_LOW)

    return errors


def validate_slots(skill: Skill, slots: Mapping[str, str]) -> list[str]:
    """Check concrete slot values against their declarations."""
    errors: list[str] = []
    for name, value in slots.items():
        slot = skill.slots.get(name)
        if slot is None:
            continue
        if slot.type == "enum":
            if value not in slot.values:
                errors.append(ENUM_VALUE_NOT_ALLOWED)
            continue
        if not slot.allow_leading_dash and value.startswith("-"):
            errors.append(DASH_LEADING_SLOT)
        if slot.max_len is not None and len(value) > slot.max_len:
            errors.append(SLOT_TOO_LONG)
    return errors
