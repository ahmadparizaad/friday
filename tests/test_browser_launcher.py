"""browser_launcher: bundle-id lookup. Headless — no browser is started."""

import subprocess

import pytest

from friday import browser_launcher


@pytest.fixture
def no_spotlight(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(browser_launcher.subprocess, "run", fake_run)


@pytest.mark.parametrize(
    "bundle_id, app",
    [
        # LaunchServices stores bundle ids lowercased; the lookup must not care.
        ("com.brave.browser", "/Applications/Brave Browser.app"),
        ("com.brave.Browser", "/Applications/Brave Browser.app"),
        ("com.google.chrome", "/Applications/Google Chrome.app"),
    ],
)
def test_fallback_path_is_case_insensitive(no_spotlight, bundle_id, app):
    assert browser_launcher.get_browser_path(bundle_id) == app


def test_unknown_bundle_id_is_none(no_spotlight):
    assert browser_launcher.get_browser_path("com.example.nope") is None


def _stub_launch(monkeypatch, tmp_path):
    captured = {}
    exe = tmp_path / "Fake.app" / "Contents" / "MacOS" / "Fake"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(browser_launcher, "get_default_browser", lambda: "x")
    monkeypatch.setattr(browser_launcher, "get_browser_path", lambda _b: str(tmp_path / "Fake.app"))

    def fake_popen(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(browser_launcher.subprocess, "Popen", fake_popen)
    return captured


def test_detach_releases_stdio_and_session(monkeypatch, tmp_path):
    captured = _stub_launch(monkeypatch, tmp_path)
    browser_launcher.launch_browser(profile_dir=tmp_path / "p", detach=True)
    kw = captured["kwargs"]
    assert kw["start_new_session"] is True
    assert kw["stdout"] is subprocess.DEVNULL and kw["stderr"] is subprocess.DEVNULL


def test_foreground_launch_keeps_inherited_stdio(monkeypatch, tmp_path):
    captured = _stub_launch(monkeypatch, tmp_path)
    browser_launcher.launch_browser(profile_dir=tmp_path / "p")
    assert captured["kwargs"] == {}


def test_cli_passes_path_profile_and_detach(monkeypatch, tmp_path):
    from pathlib import Path

    from friday import cli

    seen = {}

    class _Proc:
        pid = 1

    def fake_launch(**kwargs):
        seen.update(kwargs)
        return _Proc()

    monkeypatch.setattr(browser_launcher, "launch_browser", fake_launch)
    rc = cli._browser_cli(["launch", "--detach", "--profile", str(tmp_path)])
    assert rc == 0
    assert seen["profile_dir"] == tmp_path and isinstance(seen["profile_dir"], Path)
    assert seen["detach"] is True


# --- launch_browser -----------------------------


def _capture_cmd(monkeypatch, tmp_path, default="x"):
    seen = {}
    exe = tmp_path / "Fake.app" / "Contents" / "MacOS" / "Fake"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.touch()
    monkeypatch.setattr(browser_launcher, "get_default_browser", lambda: default)

    def fake_path(bundle):
        seen["bundle"] = bundle
        return str(tmp_path / "Fake.app")

    monkeypatch.setattr(browser_launcher, "get_browser_path", fake_path)
    monkeypatch.setattr(
        browser_launcher.subprocess, "Popen", lambda cmd, **kw: seen.setdefault("cmd", cmd)
    )
    return seen


def test_debug_port_is_bound_to_localhost_only(monkeypatch, tmp_path):
    seen = _capture_cmd(monkeypatch, tmp_path)
    browser_launcher.launch_browser(profile_dir=tmp_path / "p", debug_port=9333)
    assert "--remote-debugging-port=9333" in seen["cmd"]
    assert "--remote-debugging-address=127.0.0.1" in seen["cmd"]
    assert f"--user-data-dir={tmp_path / 'p'}" in seen["cmd"]


@pytest.mark.parametrize("port", [0, 80, 1023, 65536, True, "9222"])
def test_out_of_range_debug_port_is_refused(monkeypatch, tmp_path, port):
    _capture_cmd(monkeypatch, tmp_path)
    with pytest.raises(browser_launcher.BrowserLaunchError, match="port"):
        browser_launcher.launch_browser(profile_dir=tmp_path / "p", debug_port=port)


@pytest.mark.parametrize("default", ["com.apple.Safari", "org.mozilla.firefox"])
def test_a_browser_without_devtools_falls_back_to_chrome(monkeypatch, tmp_path, default):
    seen = _capture_cmd(monkeypatch, tmp_path, default=default)
    browser_launcher.launch_browser(profile_dir=tmp_path / "p")
    assert seen["bundle"] == "com.google.Chrome"


def test_missing_browser_is_a_clean_error(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_launcher, "get_default_browser", lambda: "x")
    monkeypatch.setattr(browser_launcher, "get_browser_path", lambda _b: None)
    with pytest.raises(browser_launcher.BrowserLaunchError, match="no app"):
        browser_launcher.launch_browser(profile_dir=tmp_path / "p")
