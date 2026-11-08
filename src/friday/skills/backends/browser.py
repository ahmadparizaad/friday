"""Browser skill backend.

Used by the web.search_here skill: a browser step's argv is a fixed verb plus slot
values, and each verb maps to one function in friday.browser. There is no
verb that navigates to a URL taken from a slot.
"""

from __future__ import annotations

from typing import Mapping

from friday.skills.backends.base import (
    DEFAULT_TIMEOUT,
    BackendError,
    BackendResult,
    fill_argv,
)


class BrowserBackend:
    name = "browser"

    def __init__(self, skill=None):
        self._skill = skill

    def run(self, step, slots: Mapping[str, str], timeout: float = DEFAULT_TIMEOUT) -> BackendResult:
        from friday import browser

        argv = fill_argv(step.argv, slots)
        verb, args = argv[0], argv[1:]
        try:
            if verb == "search_here" and len(args) == 1:
                return BackendResult(ok=True, output=browser.search_here(args[0]))
        except browser.BrowserError as exc:
            raise BackendError(str(exc)) from exc
        raise BackendError(f"unknown browser verb {verb!r} with {len(args)} argument(s)")
