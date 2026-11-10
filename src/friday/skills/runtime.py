"""The skill runtime — docs/PLAN-SKILLS.md (S1, T4).

Runs a skill's steps in order through the backends, then checks the
postcontract. The contract:

  - dry_run touches nothing and reports what would happen;
  - a failing postcondition fails the skill (no retry loop);
  - a delete step moves its target to the Bin and writes a restore
    record, never `rm`;
  - a timeout or cancel kills the subprocess and fails the step.

Every step runs through a Backend, so the argv-only, no-shell guarantee
is inherited from the backends rather than re-implemented here.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from friday.skills.backends import (
    AppleScriptBackend,
    Backend,
    BackendError,
    BrowserBackend,
    CliBackend,
    ShortcutsBackend,
    UrlBackend,
)
from friday.skills.manifest import Skill
from friday.skills.validate import validate_slots

DEFAULT_TRASH = Path.home() / ".Trash"
DEFAULT_STATE = Path.home() / ".local/state/friday-local/skills"
TIMEOUT_SECONDS = 15.0


class SkillRunError(Exception):
    """The skill could not be run at all (bad slots, unknown backend)."""


@dataclass(frozen=True)
class StepRun:
    id: str
    ok: bool
    output: str


@dataclass(frozen=True)
class SkillResult:
    ok: bool
    output: str
    steps: tuple[StepRun, ...] = ()
    postcondition_ok: bool = False


# The app runs with launchd's minimal PATH, so Homebrew is searched too.
_BINARY_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def _which(binary: str) -> str | None:
    found = shutil.which(binary)
    if found:
        return found
    for directory in _BINARY_DIRS:
        candidate = Path(directory) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _installed_apps() -> tuple[str, ...]:
    from friday.registry import known_apps

    return known_apps()


def missing_requirements(skill: Skill, *, which=_which, apps=None) -> list[str]:
    """The binaries and apps in `requires` that are not installed, in
    manifest order. Permissions cannot be checked ahead; macOS asks."""
    installed = {a.lower() for a in (apps if apps is not None else _installed_apps())}
    missing = [b for b in skill.requires.binaries if which(b) is None]
    missing += [a for a in skill.requires.apps if a.lower() not in installed]
    return missing


def default_backends(skill: Skill | None = None) -> dict[str, Backend]:
    return {
        "cli": CliBackend(),
        # the applescript backend resolves scripts in the skill folder.
        "applescript": AppleScriptBackend(skill.root if skill else None),
        "url": UrlBackend(),
        "shortcuts": ShortcutsBackend(),
        "browser": BrowserBackend(skill),
    }


def _frontmost_app() -> str:
    """The name of the frontmost application, via System Events."""
    proc = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to name of '
            "first application process whose frontmost is true",
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        shell=False,
    )
    return proc.stdout.strip()


def _check_postcondition(
    skill: Skill,
    steps: tuple[StepRun, ...],
    frontmost_app,
) -> bool:
    ptype = skill.postcondition.type
    value = skill.postcondition.value
    if ptype == "output_matches":
        if not steps:
            return False
        return re.search(value, steps[-1].output) is not None
    if ptype == "file_exists":
        path = Path(value).expanduser()
        return path.exists() or bool(glob.glob(str(path)))
    if ptype == "frontmost_app_is":
        return re.search(value, frontmost_app()) is not None
    # Unknown postcondition type: fail closed rather than assume success.
    return False


# --- review fixes ----------------------------------------------------------------

# Never handed to a later step (e.g. `open {result}`): opening one runs it.
_EXECUTABLE_SUFFIXES = frozenset(
    {".app", ".command", ".sh", ".tool", ".terminal", ".workflow", ".pkg", ".scpt"}
)


def _inside_allowed_dirs(path: Path, allowed_dirs) -> bool:
    resolved = path.expanduser().resolve()
    return any(
        resolved.is_relative_to(Path(d).expanduser().resolve()) for d in allowed_dirs
    )


def _is_executable(path: Path) -> bool:
    if path.suffix.lower() in _EXECUTABLE_SUFFIXES:
        return True
    return path.is_file() and os.access(path, os.X_OK)


def _result_slot(previous: str, allowed_dirs) -> tuple[str | None, str]:
    """The `{result}` value: the first output line naming an existing path
    inside allowed_dirs. Returns (value, error); exactly one is set."""
    for line in previous.splitlines():
        line = line.strip()
        if not line:
            continue
        path = Path(line).expanduser()
        if path.exists() and _inside_allowed_dirs(path, allowed_dirs):
            if _is_executable(path):
                return None, f"refusing to pass executable {path.name!r} to the next step"
            return str(path), ""
    return None, "no result inside the allowed directories"


def _unique(directory: Path, name: str) -> Path:
    candidate = directory / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem} {n}{suffix}"
        n += 1
    return candidate


def _move_to_trash(
    step: StepRun, trash_dir: Path, state_dir: Path, allowed_dirs
) -> str | None:
    """Move a deleted file to the Bin and write a restore record.

    Deleting means moving to the Bin, never `rm`. The restore record
    lives in the skill state dir so the file can be put back. Returns an
    error message, or None on success.
    """
    target = Path(step.output.strip()).expanduser()
    if not target.exists():
        return f"delete target {str(target)!r} does not exist"
    if not _inside_allowed_dirs(target, allowed_dirs):
        return f"refusing to delete {str(target)!r}: outside the allowed directories"
    trash_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique(trash_dir, target.name)
    shutil.move(str(target), str(destination))
    record = {
        "step": step.id,
        "original": str(target),
        "trashed": str(destination),
        "ts": time.time(),
    }
    record_path = _unique(state_dir, f"{step.id}-{time.time_ns()}.json")
    record_path.write_text(json.dumps(record, indent=2))
    return None




def run_skill(
    skill: Skill,
    slots: Mapping[str, str],
    *,
    dry_run: bool = False,
    backends: Mapping[str, Backend] | None = None,
    trash_dir: Path = DEFAULT_TRASH,
    state_dir: Path = DEFAULT_STATE,
    timeout: float = TIMEOUT_SECONDS,
    frontmost_app=_frontmost_app,
) -> SkillResult:
    """Run a skill. Raises SkillRunError only when the skill cannot be
    attempted at all; a step failure returns ok=False, never raises."""
    errors = validate_slots(skill, slots)
    if errors:
        return SkillResult(ok=False, output=f"invalid slots: {errors}")

    table = backends if backends is not None else default_backends(skill)

    if dry_run:
        # The dry run is spoken/shown from the manifest, so it needs no
        # model: fill each step's dry_run string with the slot values.
        lines = []
        for step in skill.steps:
            filled = step.dry_run
            for name, value in slots.items():
                filled = filled.replace(f"{{{name}}}", value)
            lines.append(filled)
        return SkillResult(ok=True, output="; ".join(lines), postcondition_ok=True)

    runs: list[StepRun] = []
    for step in skill.steps:
        backend = table.get(step.backend)
        if backend is None:
            return SkillResult(
                ok=False,
                output=f"step {step.id!r} has unknown backend {step.backend!r}",
                steps=tuple(runs),
            )
        step_slots = slots
        if "{result}" in step.argv:  # chaining confined to allowed_dirs
            value, error = _result_slot(runs[-1].output if runs else "", skill.allowed_dirs)
            if value is None:
                return SkillResult(ok=False, output=error, steps=tuple(runs))
            step_slots = {**slots, "result": value}
        try:
            result = backend.run(step, step_slots, timeout=timeout)
        except BackendError as exc:
            return SkillResult(
                ok=False,
                output=str(exc),
                steps=tuple(runs + [StepRun(step.id, False, str(exc))]),
            )
        run = StepRun(step.id, result.ok, result.output)
        runs.append(run)
        if not result.ok:
            return SkillResult(
                ok=False,
                output=result.output,
                steps=tuple(runs),
            )
        if step.delete:
            error = _move_to_trash(run, trash_dir, state_dir, skill.allowed_dirs)
            if error:  # a refused or impossible delete fails the skill
                return SkillResult(ok=False, output=error, steps=tuple(runs))

    post_ok = _check_postcondition(skill, tuple(runs), frontmost_app)
    output = runs[-1].output if runs else ""
    return SkillResult(
        ok=post_ok,
        output=output,
        steps=tuple(runs),
        postcondition_ok=post_ok,
    )
