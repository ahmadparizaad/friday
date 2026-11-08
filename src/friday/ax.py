"""AX perception — PLAN-M6 §M6-1 (R1, R3).

The native-app analogue of browser.py's control table: the screen is read
as an indexed Observation and acted on by integer id. The planner (M6: a
script; M7: a model) never generates a selector and never authors an
argument from screen text — screen content enters as *data* (the option
set), the step supplies only an integer, and the backend maps that integer
to an element it resolved itself.

Trust rules enforced here, not by convention:

- Every request naming an element carries the `snapshot_id` of the
  observation the id came from. The helper re-reads the tree and refuses
  if the element is no longer at that id with the same role and label
  (stale-id defense, same idea as browser.py's re-fetch).
- Secure text fields never appear with a value: the element surfaces as
  role `secure_field` with `value=None` and cannot be targeted by ax_type.
- Blocked apps (System Settings, Keychain Access, Passwords, any Privacy
  & Security pane) are refused at snapshot time.
- Bounds: max 250 elements, depth 12, label 120 chars, value 200 chars.
  Caps set `truncated=true` so the planner knows.
- The observation is never written to the audit log. The log gets a
  SHA-256 digest plus the labels of elements actually acted on.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

MAX_ELEMENTS = 250
MAX_DEPTH = 12
MAX_LABEL = 120
MAX_VALUE = 200

HELPER_TIMEOUT = 10.0

# Apps whose UI Friday never touches (PLAN-M6 §5.2): names and bundle ids.
BLOCKED_APPS = frozenset(
    {
        "System Settings",
        "System Preferences",
        "Keychain Access",
        "Passwords",
        "com.apple.systempreferences",
        "com.apple.SystemPreferences",
        "com.apple.keychainaccess",
    }
)

# Raw AX roles that are secure text entry. Surfaced as role
# "secure_field" with no value, and refused by ax_type.
SECURE_ROLES = frozenset({"AXSecureTextField", "secure_text_field", "secure_field"})

# Key combos the helper will post. Anything with quit, close-window,
# delete or empty-trash semantics is not in the list.
ALLOWED_KEYS = frozenset(
    {
        "tab",
        "shift+tab",
        "escape",
        "left",
        "right",
        "up",
        "down",
        "return",
        "cmd+n",
        "cmd+f",
        "cmd+a",
        "cmd+c",
        "cmd+v",
    }
)


class AXError(Exception):
    """The AX backend could not do what was asked."""


class NotTrusted(AXError):
    """Accessibility permission missing or revoked (§5.7)."""


class AppBlocked(AXError):
    """Snapshot of a blocked app was refused."""


class ElementStale(AXError):
    """The element id is not in this snapshot with the same role+label."""


class AXTimeout(AXError):
    """The helper did not answer in time."""


@dataclass(frozen=True)
class Element:
    id: int
    role: str
    label: str
    value: str | None
    enabled: bool
    focused: bool
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)


@dataclass(frozen=True)
class Observation:
    snapshot_id: str
    app: dict
    window_title: str | None
    elements: tuple
    source: str = "ax"
    truncated: bool = False

    def by_id(self, element_id: int) -> Element | None:
        for el in self.elements:
            if el.id == element_id:
                return el
        return None


def _strip_controls(text: str) -> str:
    return "".join(
        ch for ch in text if ch == "\n" or ch == "\t" or not unicodedata.category(ch).startswith("C")
    )


def _clean_label(text: str) -> str:
    cleaned = _strip_controls(text or "")
    cleaned = " ".join(cleaned.split())
    return cleaned[:MAX_LABEL]


def _clean_value(text: str | None) -> str | None:
    if text is None:
        return None
    return _strip_controls(text)[:MAX_VALUE]


def _canonical_role(raw_role: str) -> str:
    """Map AX role strings to the small canonical set.

    Unknown roles pass through lowercased — the classifier fails
    stricter on unknown roles, never looser.
    """
    role = (raw_role or "").strip()
    if role in SECURE_ROLES or role == "secure_field":
        return "secure_field"
    mapping = {
        "AXButton": "button",
        "AXStaticText": "static_text",
        "AXTextField": "text_field",
        "AXTextArea": "text_area",
        "AXRow": "row",
        "AXOutline": "outline",
        "AXOutlineRow": "outline_row",
        "AXCell": "cell",
        "AXTabGroup": "tab_group",
        "AXRadioButton": "radio",
        "AXCheckBox": "checkbox",
        "AXPopUpButton": "popup",
        "AXMenuItem": "menu_item",
        "AXMenuBarItem": "menu_bar_item",
        "AXDisclosureTriangle": "disclosure_triangle",
        "AXSidebar": "sidebar_item",
        "AXTable": "table",
        "AXLink": "link",
        "AXImage": "image",
    }
    if role in mapping:
        return mapping[role]
    lowered = role[2:].lower() if role.startswith("AX") else role.lower()
    return lowered or "unknown"


def build_observation(
    raw_nodes: list[dict],
    app: dict,
    window_title: str | None,
    snapshot_id: str | None = None,
) -> Observation:
    """Turn raw helper nodes into a bounded, canonical Observation.

    Pure: no I/O, so tests exercise exactly this against canned trees.
    Nodes are filtered to visible and enabled-or-focusable entries in a
    canonical order (tree order is already reading order from the helper),
    ids assigned 0..n in that order, secure values redacted.
    """
    name = app.get("name", "")
    bundle_id = app.get("bundle_id", "")
    if name in BLOCKED_APPS or bundle_id in BLOCKED_APPS:
        raise AppBlocked(f"app {name!r} is blocked")

    elements: list[Element] = []
    truncated = False
    for node in raw_nodes:
        if len(elements) >= MAX_ELEMENTS:
            truncated = True
            break
        depth = node.get("depth", 0)
        if isinstance(depth, int) and depth > MAX_DEPTH:
            truncated = True
            continue
        if node.get("hidden"):
            continue
        enabled = bool(node.get("enabled", True))
        focusable = bool(node.get("focusable", False))
        focused = bool(node.get("focused", False))
        if not (enabled or focusable or focused):
            continue
        role = _canonical_role(str(node.get("role", "")))
        label = _clean_label(str(node.get("label", "")))
        if role == "secure_field":
            value = None
        else:
            raw_value = node.get("value")
            value = _clean_value(str(raw_value)) if raw_value is not None else None
            if node.get("secure"):
                value = None
                role = "secure_field"
        bbox = node.get("bbox", [0, 0, 0, 0])
        try:
            bbox_t = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        except (TypeError, ValueError, IndexError):
            bbox_t = (0, 0, 0, 0)
        elements.append(
            Element(
                id=len(elements),
                role=role,
                label=label,
                value=value,
                enabled=enabled,
                focused=focused,
                bbox=bbox_t,
            )
        )
    if len(raw_nodes) > len(elements) and not truncated:
        # Nodes were filtered (hidden/disabled/deep), not cut: that is
        # filtering, not truncation. Only caps set the flag.
        pass
    return Observation(
        snapshot_id=snapshot_id or secrets.token_hex(8),
        app={"name": name, "bundle_id": bundle_id},
        window_title=window_title,
        elements=tuple(elements),
        source="ax",
        truncated=truncated,
    )


def observation_digest(obs: Observation) -> str:
    """SHA-256 over the canonical observation — what the audit log keeps.

    Screen contents stay out of the 90-day log; the digest plus acted-on
    labels are enough to verify a run against its fixtures.
    """
    body = {
        "app": obs.app,
        "window_title": obs.window_title,
        "elements": [
            [e.id, e.role, e.label, e.value, e.enabled, e.focused, list(e.bbox)]
            for e in obs.elements
        ],
        "truncated": obs.truncated,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def observation_to_dict(obs: Observation) -> dict:
    return {
        "snapshot_id": obs.snapshot_id,
        "app": dict(obs.app),
        "window_title": obs.window_title,
        "elements": [
            {
                "id": e.id,
                "role": e.role,
                "label": e.label,
                "value": e.value,
                "enabled": e.enabled,
                "focused": e.focused,
                "bbox": list(e.bbox),
            }
            for e in obs.elements
        ],
        "source": obs.source,
        "truncated": obs.truncated,
    }


def observation_from_dict(data: dict) -> Observation:
    elements = tuple(
        Element(
            id=int(e["id"]),
            role=str(e["role"]),
            label=str(e["label"]),
            value=e.get("value"),
            enabled=bool(e.get("enabled", True)),
            focused=bool(e.get("focused", False)),
            bbox=tuple(e.get("bbox", [0, 0, 0, 0])),
        )
        for e in data.get("elements", [])
    )
    return Observation(
        snapshot_id=str(data.get("snapshot_id", secrets.token_hex(8))),
        app=dict(data.get("app", {})),
        window_title=data.get("window_title"),
        elements=elements,
        source=str(data.get("source", "ax")),
        truncated=bool(data.get("truncated", False)),
    )


class AXBackend(Protocol):
    """What the goal loop needs from perception (PLAN-M6 §4)."""

    def snapshot(self, app: str | None = None) -> Observation:
        ...

    def press(self, snapshot_id: str, element_id: int) -> str:
        ...

    def set_value(self, snapshot_id: str, element_id: int, text: str) -> str:
        ...

    def key(self, combo: str) -> str:
        ...

    def ping(self) -> dict:
        ...

    def prompt(self) -> dict:
        """Ask the OS for the grant (only ever from `friday ax setup`)."""
        ...


class FakeAXBackend:
    """Canned snapshots. Tests inject this; production never constructs it."""

    def __init__(self, snapshots: list[Observation] | None = None):
        self._snapshots: list[Observation] = list(snapshots or [])
        self._current: int = 0
        # (snapshot_id, element_id) -> snapshot index: pressing an
        # element can switch apps, like Share opening a Mail compose.
        self.transitions: dict[tuple[str, int], int] = {}
        # (snapshot_id, element_id) -> typed value: set_value visibly
        # fills the field, so postconditions read it back. Overlaid on
        # fresh copies — the canned fixtures are never mutated in place.
        self._values: dict[tuple[str, int], str] = {}
        self.pressed: list[tuple[str, int]] = []
        self.typed: list[tuple[str, int, str]] = []
        self.keys: list[str] = []
        self.trusted = True
        self.never_answers = False

    def add(self, obs: Observation) -> None:
        self._snapshots.append(obs)

    def use(self, index: int) -> None:
        self._current = index

    @property
    def current(self) -> Observation:
        if not self._snapshots:
            raise AXError("fake backend has no snapshots")
        return self._with_values(self._snapshots[self._current])

    def _with_values(self, obs: Observation) -> Observation:
        if not self._values:
            return obs
        elements = []
        changed = False
        for el in obs.elements:
            overlay = self._values.get((obs.snapshot_id, el.id))
            if overlay is not None and el.value != overlay:
                changed = True
                elements.append(
                    Element(
                        el.id, el.role, el.label, overlay,
                        el.enabled, el.focused, el.bbox,
                    )
                )
            else:
                elements.append(el)
        if not changed:
            return obs
        return Observation(
            obs.snapshot_id, dict(obs.app), obs.window_title,
            tuple(elements), obs.source, obs.truncated,
        )

    def snapshot(self, app: str | None = None) -> Observation:
        if self.never_answers:
            raise AXTimeout("fake backend is silent")
        if not self.trusted:
            raise NotTrusted("accessibility permission missing (fake)")
        if not self._snapshots:
            raise AXError("fake backend has no snapshots")
        if app is None:
            return self.current
        for obs in reversed(self._snapshots):
            if obs.app.get("name") == app or obs.app.get("bundle_id") == app:
                return self._with_values(obs)
        raise AXError(f"fake backend has no snapshot for app {app!r}")

    def _resolve(self, snapshot_id: str, element_id: int) -> Element:
        for obs in self._snapshots:
            if obs.snapshot_id == snapshot_id:
                el = obs.by_id(element_id)
                if el is None:
                    raise ElementStale(
                        f"element {element_id} is not in snapshot {snapshot_id}"
                    )
                # Same-object re-read: the fake's tree is static, so role
                # and label trivially match. A test that wants staleness
                # supplies two snapshots with different labels.
                return el
        raise ElementStale(f"unknown snapshot {snapshot_id!r}")

    def press(self, snapshot_id: str, element_id: int) -> str:
        if self.never_answers:
            raise AXTimeout("fake backend is silent")
        if not self.trusted:
            raise NotTrusted("accessibility permission missing (fake)")
        el = self._resolve(snapshot_id, element_id)
        self.pressed.append((snapshot_id, element_id))
        target = self.transitions.get((snapshot_id, element_id))
        if target is not None and 0 <= target < len(self._snapshots):
            self._current = target
        return f"pressed [{el.id}] {el.role} {el.label!r}"

    def set_value(self, snapshot_id: str, element_id: int, text: str) -> str:
        if self.never_answers:
            raise AXTimeout("fake backend is silent")
        if not self.trusted:
            raise NotTrusted("accessibility permission missing (fake)")
        el = self._resolve(snapshot_id, element_id)
        if el.role == "secure_field":
            raise AppBlocked(f"cannot type into a secure field [{el.id}]")
        if el.role not in ("text_field", "text_area", "search_field", "combo_box"):
            raise AXError(f"cannot type into role {el.role!r}")
        self.typed.append((snapshot_id, element_id, text))
        self._values[(snapshot_id, element_id)] = text
        return f"typed into [{el.id}] {el.role} {el.label!r}"

    def key(self, combo: str) -> str:
        if combo not in ALLOWED_KEYS:
            raise AXError(f"key combo {combo!r} is not allow-listed")
        self.keys.append(combo)
        return f"pressed key {combo}"

    def ping(self) -> dict:
        return {"trusted": self.trusted}

    def prompt(self) -> dict:
        return {"trusted": self.trusted}


class HelperAXBackend:
    """Live helper. Spawns `friday-ax`, one JSON line per request.

    Request timeout with restart-on-crash: a dead helper ends the run as
    `failed` rather than hanging the loop.
    """

    def __init__(
        self,
        helper_path: Path | None = None,
        timeout: float = HELPER_TIMEOUT,
    ):
        self._helper = helper_path or default_helper_path()
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None

    def _start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                [str(self._helper)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise AXError(f"could not start friday-ax: {exc}") from exc

    def _call(self, payload: dict) -> dict:
        import select as _select  # local: only the live path needs it

        if self._proc is None or self._proc.poll() is not None:
            self._start()
        assert self._proc is not None and self._proc.stdin is not None
        assert self._proc.stdout is not None
        line = json.dumps(payload, separators=(",", ":"))
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            self._proc = None
            raise AXError(f"friday-ax write failed: {exc}") from exc
        ready, _, _ = _select.select([self._proc.stdout], [], [], self._timeout)
        if not ready:
            try:
                self._proc.kill()
            except OSError:
                pass
            self._proc = None
            raise AXTimeout(f"friday-ax did not answer within {self._timeout}s")
        reply = self._proc.stdout.readline()
        if not reply:
            self._proc = None
            raise AXError("friday-ax exited mid-request")
        try:
            data = json.loads(reply)
        except ValueError as exc:
            raise AXError(f"friday-ax answered non-JSON: {reply[:120]!r}") from exc
        if isinstance(data, dict) and data.get("error"):
            code = data.get("code", "")
            message = str(data.get("error"))
            if code == "not_trusted":
                raise NotTrusted(message)
            if code == "app_blocked":
                raise AppBlocked(message)
            if code == "element_stale":
                raise ElementStale(message)
            if code == "timeout":
                raise AXTimeout(message)
            raise AXError(message)
        return data if isinstance(data, dict) else {"result": data}

    def snapshot(self, app: str | None = None) -> Observation:
        payload: dict = {"op": "snapshot"}
        if app is not None:
            payload["app"] = app
        data = self._call(payload)
        return build_observation(
            data.get("nodes", []),
            data.get("app", {"name": "?", "bundle_id": "?"}),
            data.get("window_title"),
            snapshot_id=data.get("snapshot_id"),
        )

    def press(self, snapshot_id: str, element_id: int) -> str:
        data = self._call(
            {"op": "press", "snapshot_id": snapshot_id, "element_id": element_id}
        )
        return str(data.get("result", "pressed"))

    def set_value(self, snapshot_id: str, element_id: int, text: str) -> str:
        data = self._call(
            {
                "op": "set_value",
                "snapshot_id": snapshot_id,
                "element_id": element_id,
                "text": text,
            }
        )
        return str(data.get("result", "typed"))

    def key(self, combo: str) -> str:
        if combo not in ALLOWED_KEYS:
            raise AXError(f"key combo {combo!r} is not allow-listed")
        data = self._call({"op": "key", "combo": combo})
        return str(data.get("result", f"pressed key {combo}"))

    def ping(self) -> dict:
        try:
            data = self._call({"op": "ping"})
        except (AXError, OSError) as exc:
            raise AXError(f"friday-ax unreachable: {exc}") from exc
        return {"trusted": bool(data.get("trusted", False))}

    def prompt(self) -> dict:
        """Trigger the one-time system permission prompt.

        Only ever called from `friday ax setup` — a goal never triggers
        it (§5.7).
        """
        try:
            data = self._call({"op": "prompt"})
        except (AXError, OSError) as exc:
            raise AXError(f"friday-ax unreachable: {exc}") from exc
        return {"trusted": bool(data.get("trusted", False))}


_HELPER_REL = "Helpers/friday-ax.app/Contents/MacOS/friday-ax"


def default_helper_path(module_file: Path | None = None) -> Path:
    """Locate friday-ax: env override, then the shipped bundle, then dev build.

    Shipped: this module lives at Contents/Resources/core/friday/ax.py, so
    Contents is four levels up. Dev: repo/build/Friday.app/Contents.
    """
    override = os.environ.get("FRIDAY_AX_HELPER")
    if override:
        return Path(override)
    here = Path(module_file or __file__).resolve()
    bundled = here.parents[3] / _HELPER_REL
    if bundled.exists():
        return bundled
    return here.parents[2] / "build/Friday.app/Contents" / _HELPER_REL


_BACKEND: AXBackend | None = None


def set_backend(backend: AXBackend | None) -> None:
    """Inject a backend (tests). Production leaves this unset."""
    global _BACKEND
    _BACKEND = backend


def reset_backend() -> None:
    global _BACKEND
    _BACKEND = None


def get_backend() -> AXBackend:
    if _BACKEND is not None:
        return _BACKEND
    return HelperAXBackend()


def current_snapshot(app: str | None = None) -> Observation:
    """Fresh observation — display, not authority. Every action below
    re-resolves against the backend rather than trusting a caller's copy."""
    return get_backend().snapshot(app)
