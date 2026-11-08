"""Backend base types — docs/PLAN-SKILLS.md (S2).

Implements the contract in runtime.py and PLAN-SKILLS.md S2: "typed wrappers ... with argv-only
invocation; no shell metacharacter in a slot value can change the command".

The guarantee lives here: a step's argv is filled element by element, a
placeholder must occupy a whole element, and the result is run with
`shell=False`. A slot value is therefore always exactly one argument.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Mapping, Protocol

DEFAULT_TIMEOUT = 15.0

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class BackendError(Exception):
    """The step could not run (bad argv, missing slot, timeout)."""


@dataclass(frozen=True)
class BackendResult:
    ok: bool
    output: str


class Backend(Protocol):
    name: str

    def run(self, step, slots: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT) -> BackendResult:
        ...


def fill_argv(argv, slots: Mapping[str, str]) -> list[str]:
    """Substitute whole-element placeholders. Never concatenates."""
    filled: list[str] = []
    for element in argv:
        m = _PLACEHOLDER.fullmatch(element)
        if m:
            name = m.group(1)
            if name not in slots:
                raise BackendError(f"no value for slot {name!r}")
            filled.append(str(slots[name]))
        elif _PLACEHOLDER.search(element):
            raise BackendError(
                f"placeholder embedded in {element!r}; a slot must be a whole argument"
            )
        else:
            filled.append(element)
    return filled


def run_argv(argv: list[str], timeout: float) -> BackendResult:
    """Run an argv list with no shell; a timeout kills it and raises."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackendError(f"{argv[0]} timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise BackendError(f"could not run {argv[0]}: {exc}") from exc
    if proc.returncode != 0:
        return BackendResult(ok=False, output=(proc.stderr or proc.stdout).strip())
    return BackendResult(ok=True, output=proc.stdout.strip())
