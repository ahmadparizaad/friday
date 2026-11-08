"""Skill backends; see base.py."""

from friday.skills.backends.base import (
    Backend,
    BackendError,
    BackendResult,
    fill_argv,
)
from friday.skills.backends.browser import BrowserBackend
from friday.skills.backends.system import (
    AppleScriptBackend,
    CliBackend,
    ShortcutsBackend,
    UrlBackend,
)

__all__ = [
    "AppleScriptBackend",
    "Backend",
    "BackendError",
    "BackendResult",
    "BrowserBackend",
    "CliBackend",
    "ShortcutsBackend",
    "UrlBackend",
    "fill_argv",
]
