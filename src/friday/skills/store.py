"""The skill store — discovery, enablement, and the enabled set.

Skills are reviewable files in `skills/<id>/`. Nothing is active until
a human reviews the dry-run and runs `friday skill enable`. The enabled
set lives in `~/.config/friday-local/skills.json` (mode 0600) so it is
writable only by the user.

The store is loaded at import time: enabling a skill takes effect on the
next launch (or an explicit registry rebuild from the CLI). This keeps
the closed action space closed — a skill cannot enable itself at run
time.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from friday.skills.manifest import Skill, load_manifest

# The repo-root skills/ directory, relative to this file
# (src/friday/skills/store.py -> repo root). Overridable for tests and
# for installs that vendor skills elsewhere.
DEFAULT_SKILLS_DIR = (
    Path(__file__).parent.parent.parent.parent / "skills"
)

ENABLED_PATH = Path.home() / ".config/friday-local/skills.json"


class StoreError(Exception):
    """The store could not be read or written."""


def _skills_dir() -> Path:
    override = os.environ.get("FRIDAY_SKILLS_DIR")
    return Path(override) if override else DEFAULT_SKILLS_DIR


def discover() -> tuple[Skill, ...]:
    """Every valid skill in the skills directory, in sorted-id order.

    A malformed skill.json is skipped (and would be reported by the
    CLI's validate), never allowed to break discovery of the others.
    """
    directory = _skills_dir()
    if not directory.is_dir():
        return ()
    skills: list[Skill] = []
    for entry in sorted(directory.iterdir()):
        manifest = entry / "skill.json"
        if not manifest.exists():
            continue
        try:
            skills.append(load_manifest(manifest))
        except Exception:  # noqa: BLE001 - a broken skill must not hide its neighbours
            continue
    return tuple(skills)


# --- identity pinning ------------------------------------------------------------

# Not part of a skill's identity: editing them cannot change what runs.
# validate.SCRIPT_NOT_ALLOWED stops a script from hiding in these places.
_UNPINNED_DIRS = frozenset({"tests", "__pycache__"})
_UNPINNED_FILES = frozenset({"examples.jsonl"})


def _skill_root(skill: Skill) -> Path:
    return skill.root if skill.root is not None else _skills_dir() / skill.id


def skill_digest(skill: Skill) -> str:
    """SHA-256 over every pinned file, length-framed so moving bytes
    between two files changes the digest."""
    root = _skill_root(skill)
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if _UNPINNED_DIRS.intersection(rel.parts[:-1]) or rel.as_posix() in _UNPINNED_FILES:
            continue
        name = rel.as_posix().encode()
        data = path.read_bytes()
        for chunk in (name, data):
            h.update(len(chunk).to_bytes(8, "big"))
            h.update(chunk)
    return h.hexdigest()


def _read_state() -> tuple[set[str], dict[str, str]]:
    try:
        data = json.loads(ENABLED_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return set(), {}
    if not isinstance(data, dict):
        return set(), {}
    enabled = data.get("enabled", [])
    pins = data.get("pins", {})
    return (
        set(enabled) if isinstance(enabled, list) else set(),
        dict(pins) if isinstance(pins, dict) else {},
    )


def _write_state(enabled: set[str], pins: dict[str, str]) -> None:
    ENABLED_PATH.parent.mkdir(parents=True, exist_ok=True)
    kept = {k: v for k, v in pins.items() if k in enabled}
    ENABLED_PATH.write_text(
        json.dumps({"enabled": sorted(enabled), "pins": kept}, indent=2)
    )
    try:
        os.chmod(ENABLED_PATH, 0o600)
    except OSError:
        pass


def _still_trusted(skill: Skill, pins: dict[str, str]) -> bool:
    from friday.skills.validate import validate_manifest

    return pins.get(skill.id) == skill_digest(skill) and not validate_manifest(skill)


def enabled_skills() -> tuple[Skill, ...]:
    """The enabled skills whose files still match their pin, sorted by id."""
    enabled, pins = _read_state()
    return tuple(
        s for s in discover() if s.id in enabled and _still_trusted(s, pins)
    )


def get_enabled(skill_id: str) -> Skill | None:
    """A skill by id, only if it is enabled and still matches its pin."""
    for skill in enabled_skills():
        if skill.id == skill_id:
            return skill
    return None




def get(skill_id: str) -> Skill | None:
    """A skill by id, if it exists (enabled or not)."""
    for skill in discover():
        if skill.id == skill_id:
            return skill
    return None


def enable(skill_id: str) -> None:
    from friday.skills.validate import validate_manifest

    skill = get(skill_id)
    if skill is None:
        raise StoreError(f"unknown skill {skill_id!r}")
    errors = validate_manifest(skill)  # the validator gates enablement
    if errors:
        raise StoreError(f"{skill_id} failed validation: {', '.join(errors)}")
    enabled, pins = _read_state()
    enabled.add(skill_id)
    _write_state(enabled, {**pins, skill_id: skill_digest(skill)})


def disable(skill_id: str) -> None:
    enabled, pins = _read_state()
    enabled.discard(skill_id)
    _write_state(enabled, pins)
