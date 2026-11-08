"""CLI, AppleScript, URL and Shortcuts backends — docs/PLAN-SKILLS.md (S2).

Each backend narrows what a step can
reach on top of the argv-only guarantee in base.py:

  cli          the binary must be in validate.SKILL_BINARIES
  applescript  the script must be a validated .applescript inside the
               skill's own folder (no path escape)
  url          one http(s) or mailto URL, opened with `open`
  shortcuts    one named Shortcut, run with `shortcuts run`
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from friday.skills.backends.base import (
    DEFAULT_TIMEOUT,
    BackendError,
    BackendResult,
    fill_argv,
    run_argv,
)

URL_SCHEMES = frozenset({"https", "http", "mailto"})


class CliBackend:
    name = "cli"

    def run(self, step, slots: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT) -> BackendResult:
        from friday.skills.validate import SKILL_BINARIES

        argv = fill_argv(step.argv, slots)
        if argv[0] not in SKILL_BINARIES:
            raise BackendError(f"binary {argv[0]!r} is not allow-listed")
        return run_argv(argv, timeout)


class AppleScriptBackend:
    name = "applescript"

    def __init__(self, root: Path | None = None):
        self._root = root

    def run(self, step, slots: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT) -> BackendResult:
        from friday.skills.validate import _script_allowed

        if self._root is None:
            raise BackendError("applescript step needs its skill folder")
        if not _script_allowed(step.script):
            raise BackendError(f"script {step.script!r} is not allowed")
        root = self._root.resolve()
        script = (root / step.script).resolve()
        if not script.is_relative_to(root) or not script.is_file():
            raise BackendError(f"script {step.script!r} not found in the skill folder")
        return run_argv(["osascript", str(script), *fill_argv(step.argv, slots)], timeout)


class UrlBackend:
    name = "url"

    def run(self, step, slots: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT) -> BackendResult:
        argv = fill_argv(step.argv, slots)
        if len(argv) != 1:
            raise BackendError("url step takes exactly one URL")
        url = argv[0]
        parts = urlsplit(url)
        if parts.scheme not in URL_SCHEMES:
            raise BackendError(f"URL scheme {parts.scheme!r} is not allowed")
        if parts.scheme != "mailto" and not parts.hostname:
            raise BackendError(f"URL {url!r} has no host")
        result = run_argv(["open", url], timeout)
        return BackendResult(ok=result.ok, output=url if result.ok else result.output)


class ShortcutsBackend:
    name = "shortcuts"

    def run(self, step, slots: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT) -> BackendResult:
        argv = fill_argv(step.argv, slots)
        if len(argv) != 1 or not argv[0].strip():
            raise BackendError("shortcuts step takes exactly one Shortcut name")
        return run_argv(["shortcuts", "run", argv[0]], timeout)
