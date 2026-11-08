"""Skill manifests — docs/PLAN-SKILLS.md (S1).

A skill is a reviewable directory `skills/<id>/` whose `skill.json`
declares everything the runtime may do: typed slots with provenance,
ordered backend steps with a dry-run line each, a postcondition, and an
undo declaration. A manifest that cannot be fully described never loads.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from friday.policy import ActionClass


class ManifestError(Exception):
    """skill.json is missing, malformed, or declares something unsafe."""


# Lowercase letters, digits and underscores, dot-separated
# (e.g. files.find_open). No empty segment, no leading dot, no slash, so
# an id is always a safe single directory name.
SKILL_ID_RE = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)*")

SLOT_TYPES = frozenset({"file", "contact", "app", "duration", "text", "enum"})

# Where a slot value is allowed to come from (PLAN-SKILLS.md, "Provenance").
PROVENANCE = frozenset({"utterance", "resolver", "enum"})

BACKENDS = frozenset({"cli", "applescript", "url", "shortcuts", "browser"})

_CLASS_RANK = {
    ActionClass.READ: 0,
    ActionClass.NAVIGATE: 1,
    ActionClass.MUTATE_LOCAL: 2,
    ActionClass.MUTATE_REMOTE: 3,
    ActionClass.DESTRUCTIVE: 4,
    ActionClass.BLOCKED: 5,
}


@dataclass(frozen=True)
class Slot:
    name: str
    type: str
    provenance: str
    max_len: int | None = None
    values: tuple[str, ...] = ()
    default: str | None = None
    allow_leading_dash: bool = False


@dataclass(frozen=True)
class Step:
    id: str
    backend: str
    argv: tuple[str, ...]
    dry_run: str
    delete: bool = False
    action_class: ActionClass | None = None
    script: str | None = None  # allow-listed .applescript, if backend needs one


@dataclass(frozen=True)
class Postcondition:
    type: str
    value: str


@dataclass(frozen=True)
class Requires:
    binaries: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class Skill:
    id: str
    version: str
    title: str
    triggers: tuple[str, ...]
    slots: Mapping[str, Slot] = field(
        default_factory=lambda: MappingProxyType({})
    )
    action_class: ActionClass = ActionClass.READ
    requires: Requires = field(default_factory=Requires)
    allowed_dirs: tuple[str, ...] = ()
    steps: tuple[Step, ...] = ()
    postcondition: Postcondition = field(
        default_factory=lambda: Postcondition(type="", value="")
    )
    undo: str = "none"
    # the skill's folder, set by load_manifest; None when built in code.
    root: Path | None = None

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Skill):
            return NotImplemented
        return self.id == other.id


def _require_str(data: dict, key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}: {key!r} must be a non-empty string")
    return value


def _build(data: dict) -> Skill:
    where = "skill.json"

    skill_id = _require_str(data, "id", where)
    where = skill_id
    version = _require_str(data, "version", where)
    title = _require_str(data, "title", where)

    try:
        action_class = ActionClass(data["action_class"])
    except (KeyError, ValueError) as exc:
        raise ManifestError(
            f"{where}: action_class {data.get('action_class')!r} is not a "
            f"known ActionClass"
        ) from exc

    raw_triggers = data.get("triggers")
    if not isinstance(raw_triggers, list) or not raw_triggers:
        raise ManifestError(f"{where}: triggers must be a non-empty list")
    triggers = tuple(str(t) for t in raw_triggers)

    raw_slots = data.get("slots", {})
    if not isinstance(raw_slots, dict):
        raise ManifestError(f"{where}: slots must be an object")
    slots: dict[str, Slot] = {}
    for name, spec in raw_slots.items():
        if not isinstance(spec, dict):
            raise ManifestError(f"{where}: slot {name!r} must be an object")
        slot_type = spec.get("type")
        if slot_type not in SLOT_TYPES:
            raise ManifestError(
                f"{where}: slot {name!r} has unknown type {slot_type!r}"
            )
        provenance = spec.get("provenance")
        if provenance not in PROVENANCE:
            raise ManifestError(
                f"{where}: slot {name!r} has no valid provenance "
                f"{provenance!r} (need one of {sorted(PROVENANCE)})"
            )
        max_len = spec.get("max_len")
        if max_len is not None and (
            isinstance(max_len, bool) or not isinstance(max_len, int) or max_len <= 0
        ):
            raise ManifestError(
                f"{where}: slot {name!r} max_len must be a positive int"
            )
        values = ()
        if slot_type == "enum":
            raw_values = spec.get("values")
            if not isinstance(raw_values, list) or not raw_values:
                raise ManifestError(
                    f"{where}: enum slot {name!r} requires a non-empty "
                    f"'values' list"
                )
            values = tuple(str(v) for v in raw_values)
        slots[name] = Slot(
            name=name,
            type=slot_type,
            provenance=provenance,
            max_len=max_len,
            values=values,
            default=spec.get("default"),
            allow_leading_dash=bool(spec.get("allow_leading_dash", False)),
        )

    for trigger in triggers:
        for placeholder in re.findall(r"\{(\w+)\}", trigger):
            if placeholder not in slots:
                raise ManifestError(
                    f"{where}: trigger {trigger!r} names unknown slot "
                    f"{placeholder!r}"
                )

    raw_requires = data.get("requires", {})
    if not isinstance(raw_requires, dict):
        raise ManifestError(f"{where}: requires must be an object")
    requires = Requires(
        binaries=tuple(str(b) for b in raw_requires.get("binaries", [])),
        apps=tuple(str(a) for a in raw_requires.get("apps", [])),
        permissions=tuple(str(p) for p in raw_requires.get("permissions", [])),
    )

    raw_dirs = data.get("allowed_dirs", [])
    if not isinstance(raw_dirs, list):
        raise ManifestError(f"{where}: allowed_dirs must be a list")
    allowed_dirs = tuple(str(d) for d in raw_dirs)

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ManifestError(f"{where}: steps must be a non-empty list")
    steps: list[Step] = []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            raise ManifestError(f"{where}: each step must be an object")
        step_id = _require_str(raw, "id", where)
        backend = raw.get("backend")
        if backend not in BACKENDS:
            raise ManifestError(
                f"{where}: step {step_id!r} has unknown backend {backend!r} "
                f"(need one of {sorted(BACKENDS)})"
            )
        raw_argv = raw.get("argv")
        if not isinstance(raw_argv, list) or not raw_argv:
            raise ManifestError(
                f"{where}: step {step_id!r} argv must be a non-empty list"
            )
        dry_run = _require_str(raw, "dry_run", where)
        step_class = raw.get("action_class")
        if step_class is not None:
            try:
                step_class = ActionClass(step_class)
            except ValueError as exc:
                raise ManifestError(
                    f"{where}: step {step_id!r} action_class {step_class!r} "
                    f"is not known"
                ) from exc
        script = raw.get("script")
        if script is not None and (
            not isinstance(script, str) or not script.strip()
        ):
            raise ManifestError(
                f"{where}: step {step_id!r} script must be a non-empty string"
            )
        steps.append(
            Step(
                id=step_id,
                backend=backend,
                argv=tuple(str(a) for a in raw_argv),
                dry_run=dry_run,
                delete=bool(raw.get("delete", False)),
                action_class=step_class,
                script=script,
            )
        )

    raw_post = data.get("postcondition")
    if not isinstance(raw_post, dict):
        raise ManifestError(f"{where}: postcondition must be an object")
    post_type = raw_post.get("type")
    post_value = raw_post.get("value")
    if not isinstance(post_type, str) or not post_type.strip():
        raise ManifestError(f"{where}: postcondition.type must be non-empty")
    if not isinstance(post_value, str) or not post_value.strip():
        raise ManifestError(f"{where}: postcondition.value must be non-empty")
    postcondition = Postcondition(type=post_type, value=post_value)

    undo = data.get("undo")
    if not isinstance(undo, str) or not undo.strip():
        if _CLASS_RANK[action_class] < _CLASS_RANK[ActionClass.DESTRUCTIVE]:
            raise ManifestError(
                f"{where}: undo is required when action_class is below "
                f"destructive"
            )
        undo = "none"

    return Skill(
        id=skill_id,
        version=version,
        title=title,
        triggers=triggers,
        slots=MappingProxyType(slots),
        action_class=action_class,
        requires=requires,
        allowed_dirs=allowed_dirs,
        steps=tuple(steps),
        postcondition=postcondition,
        undo=undo,
    )


def load_manifest(path: Path) -> Skill:
    """Load and validate a skill.json. Raises ManifestError on any
    problem — a skill that cannot be fully described never runs."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read skill.json: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("skill.json must be a JSON object")
    return replace(_build(data), root=Path(path).parent)
