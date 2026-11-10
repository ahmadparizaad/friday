"""Settings persistence: autonomy and audit retention survive a restart."""

import json

import pytest

import friday.settings as settings_mod
from friday.audit import DEFAULT_RETENTION_DAYS, MAX_RETENTION_DAYS, MIN_RETENTION_DAYS
from friday.policy import Autonomy
from friday.settings import Settings, _coerce_retention_days


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(Settings, "_path", staticmethod(lambda: path))
    return path


def test_defaults_are_cautious_with_ninety_day_retention():
    s = Settings()
    assert Autonomy(s.autonomy) is Autonomy.CAUTIOUS
    assert s.audit_retention_days == DEFAULT_RETENTION_DAYS


def test_round_trip_through_disk(settings_path):
    Settings(autonomy="strict", audit_retention_days=30).save()
    loaded = Settings.load()
    assert loaded.autonomy == "strict"
    assert loaded.audit_retention_days == 30


def test_settings_file_from_an_older_build_still_loads(settings_path):
    settings_path.write_text(json.dumps({"mic_enabled": False, "push_to_talk_key": "fn"}))
    loaded = Settings.load()
    assert loaded.mic_enabled is False
    assert loaded.autonomy == "cautious"
    assert loaded.audit_retention_days == DEFAULT_RETENTION_DAYS


@pytest.mark.parametrize("bad", ["yolo", "", None, 3, "PERMISSIVE "])
def test_an_unknown_autonomy_falls_back_to_cautious_not_permissive(bad):
    assert Settings.from_dict({"autonomy": bad}).autonomy == "cautious"


@pytest.mark.parametrize(
    "raw, expected",
    [
        (30, 30),
        ("45", 45),
        (1, MIN_RETENTION_DAYS),
        (10_000, MAX_RETENTION_DAYS),
        ("abc", DEFAULT_RETENTION_DAYS),
        (None, DEFAULT_RETENTION_DAYS),
        (True, DEFAULT_RETENTION_DAYS),
        (12.9, 12),
    ],
)
def test_retention_is_clamped_to_the_audit_bounds(raw, expected):
    assert _coerce_retention_days(raw) == expected
    assert Settings.from_dict({"audit_retention_days": raw}).audit_retention_days == expected
