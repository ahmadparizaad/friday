"""Launch a Chromium browser with Friday's profile and a local debug port.

The browser tools (browser.py) drive a tab over the Chrome DevTools
Protocol, which needs the browser started with --remote-debugging-port.
This module starts the user's default browser that way, in a separate
profile so Friday never touches the user's everyday browsing session.
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path


def get_default_browser() -> str:
    """Get the user's default browser bundle identifier.

    Reads from ~/Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist
    """
    plist_path = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    if not plist_path.exists():
        # Fallback to Chrome
        return "com.google.Chrome"

    try:
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)

        # Look for the https handler
        handlers = plist.get("LSHandlers", [])
        for handler in handlers:
            if handler.get("LSHandlerURLScheme") == "https":
                return handler.get("LSHandlerRoleAll", "com.google.Chrome")
    except Exception:
        pass

    return "com.google.Chrome"


def get_browser_path(bundle_id: str) -> str | None:
    """Get the full path to the browser app from its bundle identifier."""
    try:
        result = subprocess.run(
            ["mdfind", f"kMDItemCFBundleIdentifier == '{bundle_id}'"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        paths = result.stdout.strip().split("\n")
        for path in paths:
            if path.endswith(".app"):
                return path
    except Exception:
        pass

    # Common fallback paths
    common_paths = {
        "com.google.Chrome": "/Applications/Google Chrome.app",
        "com.brave.Browser": "/Applications/Brave Browser.app",
        "com.microsoft.edgemac": "/Applications/Microsoft Edge.app",
        "org.mozilla.firefox": "/Applications/Firefox.app",
        "com.apple.Safari": "/Applications/Safari.app",
    }
    # LaunchServices stores bundle ids lowercased.
    by_lower = {k.lower(): v for k, v in common_paths.items()}
    return by_lower.get(bundle_id.lower())


def get_browser_executable(app_path: str) -> str:
    """Get the executable path inside the .app bundle."""
    return f"{app_path}/Contents/MacOS/{Path(app_path).stem}"


def get_profile_dir() -> Path:
    """Get the Friday browser profile directory."""
    return Path.home() / "Library/Application Support/friday/browser-profile"



# Browsers without the Chrome DevTools Protocol; Friday falls back to Chrome.
_NO_CDP = frozenset({"com.apple.safari", "org.mozilla.firefox"})


class BrowserLaunchError(Exception):
    """No usable Chromium browser could be started."""


def launch_browser(
    profile_dir: Path | None = None,
    debug_port: int = 9222,
    extra_args: list[str] | None = None,
    detach: bool = False,
) -> subprocess.Popen:
    """Launch the default browser with Friday profile and debug port.

    The debug port is bound to 127.0.0.1 only: anything that can reach it
    can drive the browser. With detach=True the browser gets its own
    session and no inherited stdio, so it outlives the terminal.
    """
    if isinstance(debug_port, bool) or not isinstance(debug_port, int) or not 1024 <= debug_port <= 65535:
        raise BrowserLaunchError(f"debug port must be 1024-65535, got {debug_port!r}")
    bundle_id = get_default_browser()
    if bundle_id.lower() in _NO_CDP:
        bundle_id = "com.google.Chrome"
    app_path = get_browser_path(bundle_id)
    if app_path is None:
        raise BrowserLaunchError(f"no app found for browser {bundle_id!r}")
    executable = get_browser_executable(app_path)
    if not Path(executable).exists():
        raise BrowserLaunchError(f"browser executable not found: {executable}")

    profile = Path(profile_dir) if profile_dir is not None else get_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        executable,
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        *(extra_args or []),
    ]
    if not detach:
        return subprocess.Popen(cmd)
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
