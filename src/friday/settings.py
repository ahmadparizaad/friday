"""Settings persistence for friday-local."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from friday.audit import DEFAULT_RETENTION_DAYS, MAX_RETENTION_DAYS, MIN_RETENTION_DAYS
from friday.policy import Autonomy

DEFAULT_AUTONOMY = Autonomy.CAUTIOUS.value


def _coerce_autonomy(value) -> str:
    """A level from the settings file, or the default. An unreadable value
    falls back to cautious, never to the permissive end."""
    try:
        return Autonomy(value).value
    except ValueError:
        return DEFAULT_AUTONOMY


def _coerce_retention_days(value) -> int:
    """Retention in whole days, clamped to the audit log's bounds (M4)."""
    if isinstance(value, bool):
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, days))


@dataclass
class Settings:
    mic_enabled: bool = True
    push_to_talk_key: str = "fn"
    strip_visible: bool = True
    auto_hide_when_idle: bool = False
    confirm_timeout: float = 120.0
    reduce_motion: bool = False
    autonomy: str = DEFAULT_AUTONOMY
    audit_retention_days: int = DEFAULT_RETENTION_DAYS

    def to_dict(self) -> dict:
        return {
            "mic_enabled": self.mic_enabled,
            "push_to_talk_key": self.push_to_talk_key,
            "strip_visible": self.strip_visible,
            "auto_hide_when_idle": self.auto_hide_when_idle,
            "confirm_timeout": self.confirm_timeout,
            "reduce_motion": self.reduce_motion,
            "autonomy": self.autonomy,
            "audit_retention_days": self.audit_retention_days,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        return cls(
            mic_enabled=data.get("mic_enabled", True),
            push_to_talk_key=data.get("push_to_talk_key", "fn"),
            strip_visible=data.get("strip_visible", True),
            auto_hide_when_idle=data.get("auto_hide_when_idle", False),
            confirm_timeout=data.get("confirm_timeout", 120.0),
            reduce_motion=data.get("reduce_motion", False),
            autonomy=_coerce_autonomy(data.get("autonomy", DEFAULT_AUTONOMY)),
            audit_retention_days=_coerce_retention_days(
                data.get("audit_retention_days", DEFAULT_RETENTION_DAYS)
            ),
        )

    def save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @staticmethod
    def load() -> Settings:
        path = Settings._path()
        if path.exists():
            try:
                return Settings.from_dict(json.loads(path.read_text()))
            except (json.JSONDecodeError, ValueError):
                pass
        return Settings()

    @staticmethod
    def _path() -> Path:
        return Path.home() / ".config/friday-local/settings.json"
