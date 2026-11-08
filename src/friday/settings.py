"""Settings persistence for friday-local."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    mic_enabled: bool = True
    push_to_talk_key: str = "fn"
    strip_visible: bool = True
    auto_hide_when_idle: bool = False
    confirm_timeout: float = 120.0
    reduce_motion: bool = False

    def to_dict(self) -> dict:
        return {
            "mic_enabled": self.mic_enabled,
            "push_to_talk_key": self.push_to_talk_key,
            "strip_visible": self.strip_visible,
            "auto_hide_when_idle": self.auto_hide_when_idle,
            "confirm_timeout": self.confirm_timeout,
            "reduce_motion": self.reduce_motion,
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
