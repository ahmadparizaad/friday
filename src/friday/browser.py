"""CDP browser control table — ARCHITECTURE.md §5.6, M2 (R1, R3).

The page is read as an **indexed control table** and acted on by index.
The model never generates a selector and never authors an argument from
page text: page content enters as *data* (the option set), the transcript
supplies only an integer, and the backend maps that integer to a node it
resolved itself. That is a Choice over a fixed set, which is laya's shape.

Trust rules enforced here, not by convention:

- No navigation to a spoken URL. These tools operate on the **current
  tab only**; opening URLs stays behind `open_url`'s shortcut table.
  There is deliberately no `browser.goto(url)` in this module.
- Every operation re-fetches a fresh table at execution time and
  validates the index against it. A snapshot the user saw is display,
  not authority — the page may have changed since (TOCTOU).
- `submit_control` and `click_control` share one mechanism. They are
  separate tools only so the registry can put them in different policy
  classes (`navigate` vs `mutate_remote` with its verbatim-readback
  confirm floor, §5.4).

Transport is stdlib-only: HTTP to the browser's remote-debugging port
for target discovery plus a minimal websocket client for CDP. No new
dependency, so the headless suite stays hermetic — tests inject
`FakeBackend` and never touch a real browser.
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import subprocess
import urllib.request
from dataclasses import dataclass, field

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
_TIMEOUT = 10.0

# Roles worth an index. Actionable roles are clickable; text roles exist
# so `browser_read` has something to read. Anything else (generic
# containers, layout) is noise the table omits.
INCLUDED_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "checkbox",
        "radio",
        "combobox",
        "listbox",
        "menuitem",
        "tab",
        "switch",
        "spinbutton",
        "heading",
        "StaticText",
        "paragraph",
        "image",
    }
)

# An unbounded page must not become an unbounded option set.
MAX_CONTROLS = 50

# Closed search templates behind `browser_search` (M3, §5.3) — the same
# trust tier as shortcuts.py: adding a site is a reviewed act. The query
# is a literal transcript span, URL-encoded, never interpreted.
SEARCH_SITES: dict[str, str] = {
    "google": "https://www.google.com/search?q={q}",
    "github": "https://github.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "wikipedia": "https://en.wikipedia.org/wiki/Special:Search?search={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
}


class BrowserError(Exception):
    """The browser could not be reached or the index is unusable."""


class BrowserUnreachable(BrowserError):
    """No browser is listening on the debug port."""


@dataclass(frozen=True)
class Control:
    index: int
    role: str
    name: str
    backend_id: int  # CDP backendDOMNodeId; FakeBackend uses index


def build_table(ax_nodes: list[dict]) -> list[Control]:
    """Turn raw `Accessibility.getFullAXTree` nodes into the table.

    Pure: no I/O, so tests exercise exactly this against canned trees.
    A node counts only if its role is included *and* it has a name —
    an unnamed container is layout, not a control. Indices are assigned
    in tree order and capped at MAX_CONTROLS.
    """
    controls: list[Control] = []
    for node in ax_nodes:
        if node.get("ignored"):
            continue
        role = node.get("role", {}).get("value", "")
        if role not in INCLUDED_ROLES:
            continue
        name = (node.get("name", {}) or {}).get("value", "") or ""
        name = " ".join(name.split())
        if not name:
            continue
        backend_id = node.get("backendDOMNodeId", -1)
        controls.append(
            Control(
                index=len(controls),
                role=role,
                name=name[:200],
                backend_id=backend_id,
            )
        )
        if len(controls) >= MAX_CONTROLS:
            break
    return controls


def format_table(controls: list[Control]) -> str:
    """The snapshot text shown to the user and recorded in the audit log."""
    if not controls:
        return "page has no readable controls"
    return "\n".join(f"[{c.index}] {c.role} {c.name!r}" for c in controls)


# --- minimal websocket client (stdlib only) ---------------------------------
#
# CDP speaks websocket; Python 3.12 ships no client, and M2 must not add
# a dependency for one small channel. This implements just enough:
# single masked text sends, fragmented/unfragmented server frames,
# ping/pong, close. Anything else raises BrowserError rather than
# misparsing.


def _ws_key() -> tuple[str, str]:
    import os

    raw = base64.b64encode(os.urandom(16)).decode()
    accept = base64.b64encode(
        hashlib.sha1(
            (raw + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
        ).digest()
    ).decode()
    return raw, accept


class _WS:
    def __init__(self, url: str):
        from urllib.parse import urlparse

        parts = urlparse(url.replace("ws://", "http://"))
        self._sock = socket.create_connection(
            (parts.hostname or CDP_HOST, parts.port or CDP_PORT), _TIMEOUT
        )
        self._sock.settimeout(_TIMEOUT)
        self._buf = b""
        key, accept = _ws_key()
        path = parts.path or "/"
        self._sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {parts.hostname}:{parts.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            head += chunk
        if b" 101 " not in head or accept.encode() not in head:
            raise BrowserError(f"websocket handshake failed: {head[:120]!r}")

    def send(self, text: str) -> None:
        import os

        payload = text.encode()
        mask = os.urandom(4)
        if len(payload) < 126:
            header = bytes([0x81, 0x80 | len(payload)])
            frame = header + mask
        else:
            header = bytes([0x81, 0x80 | 126]) + struct.pack("!H", len(payload))
            frame = header + mask
        frame += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(frame)

    def _fill(self, n: int) -> None:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise BrowserError("websocket closed mid-frame")
            self._buf += chunk

    def recv(self) -> str:
        """Next complete text message, answering pings on the way."""
        message = b""
        while True:
            self._fill(2)
            b1, b2 = self._buf[0], self._buf[1]
            opcode = b1 & 0x0F
            length = b2 & 0x7F
            at = 2
            if length == 126:
                self._fill(4)
                (length,) = struct.unpack("!H", self._buf[2:4])
                at = 4
            elif length == 127:
                self._fill(10)
                (length,) = struct.unpack("!Q", self._buf[2:10])
                at = 10
            self._fill(at + length)
            payload = self._buf[at : at + length]
            self._buf = self._buf[at + length :]
            if opcode == 0x8:
                raise BrowserError("websocket closed by browser")
            if opcode == 0x9:  # ping -> pong
                self._sock.sendall(bytes([0x8A, len(payload)]) + payload)
                continue
            if opcode == 0xA:  # unsolicited pong
                continue
            if opcode in (0x1, 0x0):  # text / continuation
                message += payload
                if b1 & 0x80:  # FIN
                    return message.decode("utf-8", errors="replace")
                continue
            raise BrowserError(f"unexpected websocket opcode {opcode}")

    def close(self) -> None:
        try:
            self._sock.sendall(bytes([0x88, 0x00]))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


# --- CDP backend --------------------------------------------------------------


def _http_json(path: str, method: str = "GET", body: bytes | None = None):
    req = urllib.request.Request(
        f"http://{CDP_HOST}:{CDP_PORT}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except OSError as exc:
        raise BrowserUnreachable(  # subclass, so callers can fall back
            f"browser not reachable at {CDP_HOST}:{CDP_PORT} "
            f"(launch with --remote-debugging-port={CDP_PORT}): {exc}"
        ) from exc


class _CDPBackend:
    """Live browser. One websocket per operation — simple, and CDP on
    localhost answers in milliseconds, so nothing here is the slow path."""

    def _connect(self) -> _WS:
        targets = _http_json("/json/list")
        target = pick_target(targets)
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise BrowserError("page has no debugger URL")
        return _WS(ws_url)

    @staticmethod
    def _roundtrip(ws: _WS, cmd_id: int, method: str, params: dict) -> dict:
        ws.send(json.dumps({"id": cmd_id, "method": method, "params": params}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == cmd_id:
                if "error" in msg:
                    raise BrowserError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})
            # else: a CDP event (e.g. execution context update) — ignore

    def _call(self, method: str, params: dict | None = None) -> dict:
        """One command on its own connection — for single-shot calls."""
        ws = self._connect()
        try:
            return self._roundtrip(ws, 1, method, params or {})
        finally:
            ws.close()

    def ax_tree(self) -> list[dict]:
        return self._call("Accessibility.getFullAXTree").get("nodes", [])

    def _object_id(self, ws: _WS, cmd_id: int, backend_id: int) -> str:
        result = self._roundtrip(
            ws, cmd_id, "DOM.resolveNode", {"backendNodeId": backend_id}
        )
        object_id = ((result.get("object") or {}).get("objectId")) or ""
        if not object_id:
            raise BrowserError(f"control {backend_id} no longer resolves")
        return object_id

    def click(self, backend_id: int) -> str:
        # resolveNode + callFunctionOn share ONE connection: a remote
        # object id is only valid in the session that created it, so two
        # separate connections (as an earlier version did) fail with
        # "Could not find object with given id" — found live, not by test.
        ws = self._connect()
        try:
            object_id = self._object_id(ws, 1, backend_id)
            result = self._roundtrip(
                ws,
                2,
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": (
                        "function(){"
                        "try{this.scrollIntoView({block:'nearest'});}catch(e){}"
                        "try{this.focus();}catch(e){}"
                        "this.click();"
                        "return (this.innerText||this.value||'').slice(0,200);"
                        "}"
                    ),
                    "returnByValue": True,
                },
            )
        finally:
            ws.close()
        return (result.get("result") or {}).get("value", "") or ""

    def text(self, backend_id: int) -> str:
        ws = self._connect()
        try:
            object_id = self._object_id(ws, 1, backend_id)
            result = self._roundtrip(
                ws,
                2,
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": (
                        "function(){return (this.innerText||this.value||'')"
                        ".slice(0,2000);}"
                    ),
                    "returnByValue": True,
                },
            )
        finally:
            ws.close()
        return (result.get("result") or {}).get("value", "") or ""

    def navigate(self, url: str) -> str:
        """Navigate the current tab to a URL the core constructed itself.

        Only called with a SEARCH_SITES template + encoded literal span
        (see search() below) — never with a spoken or page-supplied URL,
        so there is still no browser.goto(url) primitive for anyone to
        reach (§5.6).
        """
        self._call("Page.navigate", {"url": url})
        return url


    def get_current_url(self) -> str:
        return pick_target(_http_json("/json/list")).get("url", "") or ""

    def new_tab(self, url: str) -> str:
        from urllib.parse import quote

        _http_json(f"/json/new?{quote(url, safe=':/?=&%')}", method="PUT")
        return url

    def close_tab(self) -> str:
        target = pick_target(_http_json("/json/list"))
        tab_id = target.get("id")
        if not tab_id:
            raise BrowserError("current tab has no id")
        req = urllib.request.Request(f"http://{CDP_HOST}:{CDP_PORT}/json/close/{tab_id}")
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT):
                pass
        except OSError as exc:
            raise BrowserUnreachable(f"could not close tab: {exc}") from exc
        return target.get("url", "")


def pick_target(targets: list[dict]) -> dict:
    """Which tab M2 acts on: the first http(s) page, else the first
    non-empty page that is not a fresh about:blank, else the first page.

    Pure, so the preference order is unit-tested rather than trusted.
    Deterministic by list order in every tier — "current tab" is a
    heuristic over CDP's list, and a heuristic that sometimes picks a
    different tab is worse than one that always picks the same kind.
    """
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise BrowserError("browser has no open page")
    for page in pages:
        if (page.get("url") or "").startswith(("http://", "https://")):
            return page
    for page in pages:
        if (page.get("url") or "") not in ("", "about:blank"):
            return page
    return pages[0]


# --- FakeBackend (tests) --------------------------------------------------------


class FakeBackend:
    """Canned page. Tests inject this via set_backend; production never
    constructs it."""

    def __init__(self, nodes: list[dict] | None = None):
        self._nodes = nodes if nodes is not None else _CANNED_NODES
        self.clicked: list[int] = []
        self.navigated: list[str] = []

    def ax_tree(self) -> list[dict]:
        return self._nodes

    def click(self, backend_id: int) -> str:
        self.clicked.append(backend_id)
        return f"clicked {backend_id}"

    def navigate(self, url: str) -> str:
        self.navigated.append(url)
        return url

    # tab operations for navigate_tab / close_tab tests.
    def get_current_url(self) -> str:
        return "https://example.com/"

    def new_tab(self, url: str) -> str:
        self.opened_tabs = getattr(self, "opened_tabs", []) + [url]
        return url

    def close_tab(self) -> str:
        self.closed_tabs = getattr(self, "closed_tabs", 0) + 1
        return "https://example.com/"

    def text(self, backend_id: int) -> str:
        for node in self._nodes:
            if node.get("backendDOMNodeId") == backend_id:
                return (node.get("name", {}) or {}).get("value", "")
        raise BrowserError(f"control {backend_id} not in canned page")


def _ax(role: str, name: str, backend_id: int, ignored: bool = False) -> dict:
    return {
        "backendDOMNodeId": backend_id,
        "ignored": ignored,
        "role": {"value": role},
        "name": {"value": name},
    }


_CANNED_NODES = [
    _ax("button", "Sign in", 101),
    _ax("link", "Docs", 102),
    _ax("textbox", "Search", 103),
    _ax("StaticText", "Welcome to Example", 104),
    _ax("generic", "layout wrapper", 105),  # excluded: role not included
    _ax("button", "   ", 106),  # excluded: unnamed
    _ax("button", "Hidden", 107, ignored=True),  # excluded: ignored
]


# --- module API ------------------------------------------------------------------

_BACKEND: _CDPBackend | FakeBackend | None = None


def set_backend(backend) -> None:
    """Inject a backend (tests). Production leaves this unset."""
    global _BACKEND
    _BACKEND = backend


def reset_backend() -> None:
    global _BACKEND
    _BACKEND = None


def _get_backend():
    return _BACKEND if _BACKEND is not None else _CDPBackend()


def controls() -> list[Control]:
    """The current table, freshly fetched. Display, not authority — every
    action below re-fetches rather than trusting a caller's copy."""
    return build_table(_get_backend().ax_tree())


def control_count() -> int:
    return len(controls())


def snapshot() -> str:
    return format_table(controls())


def _resolve(index: int) -> Control:
    if isinstance(index, bool) or not isinstance(index, int):
        raise BrowserError(f"control index must be an integer, got {index!r}")
    if index < 0:
        raise BrowserError(f"control index must be >= 0, got {index}")
    table = controls()  # fresh fetch: the TOCTOU bound
    if index >= len(table):
        raise BrowserError(
            f"control {index} is not on the page "
            f"({len(table)} controls right now — snapshot first)"
        )
    return table[index]


def read_control(index: int) -> str:
    control = _resolve(index)
    return _get_backend().text(control.backend_id) or control.name


def click_control(index: int) -> str:
    control = _resolve(index)
    _get_backend().click(control.backend_id)
    return f"clicked [{control.index}] {control.role} {control.name!r}"


def submit_control(index: int) -> str:
    """Same mechanism as click; a separate function so the audit log and
    the registry's action class distinguish a form-affecting submit
    (mutate_remote, always confirms) from a navigation click."""
    control = _resolve(index)
    _get_backend().click(control.backend_id)
    return f"submitted [{control.index}] {control.role} {control.name!r}"


def search_url(site: str, query: str) -> str:
    """Build the search URL without touching the browser (pure)."""
    from urllib.parse import quote

    template = SEARCH_SITES.get(site)
    if template is None:
        raise BrowserError(f"unknown search site {site!r}")
    if not isinstance(query, str) or not query.strip():
        raise BrowserError("search query is empty")
    if len(query) > 500:
        raise BrowserError("search query is too long")
    return template.format(q=quote(query.strip(), safe=""))


def search(site: str, query: str) -> str:
    """Search a closed-template site for a literal query (M3, §5.3).

    Navigates the current tab only — the same tab pick_target() would
    act on — to a URL built from a reviewed template plus the encoded
    literal span. Page text never participates.
    """
    url = search_url(site, query)
    _get_backend().navigate(url)
    return f"searched {site} for {query!r}"


# --- search the current site, new/close tab --------------------------------------

# Registrable domain -> SEARCH_SITES key. A host matches only itself or a
# subdomain of it, so evilyoutube.com and youtube.com.evil.example do not.
SITE_DOMAINS: dict[str, str] = {
    "google.com": "google",
    "github.com": "github",
    "youtube.com": "youtube",
    "wikipedia.org": "wikipedia",
    "stackoverflow.com": "stackoverflow",
}

# Frontmost app -> AppleScript returning its current tab URL (read-only).
_FRONT_URL_SCRIPTS: dict[str, str] = {
    "Google Chrome": 'tell application "Google Chrome" to return URL of active tab of front window',
    "Brave Browser": 'tell application "Brave Browser" to return URL of active tab of front window',
    "Safari": 'tell application "Safari" to return URL of front document',
}

# Spoken browser word -> app, for navigate_tab's fallback.
TAB_BROWSERS: dict[str, str] = {
    "chrome": "Google Chrome",
    "brave": "Brave Browser",
    "brew": "Brave Browser",
    "safari": "Safari",
}

_STT_TRAILING = ".!?,;:"


def site_for_url(url: str) -> str | None:
    """The SEARCH_SITES key for a page URL, or None if it is not one."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url or "")
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    for domain, site in SITE_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return site
    return None


def _run(argv: list[str]) -> str:
    """argv-only subprocess; every failure becomes a BrowserError."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrowserError(f"{argv[0]} failed: {exc}") from exc
    if proc.returncode != 0:
        raise BrowserError(f"{argv[0]} failed: {(proc.stderr or '').strip() or proc.returncode}")
    return (proc.stdout or "").strip()


def _front_browser_url() -> str:
    """URL of the frontmost known browser's tab, without a debug port.

    Known limitation (kept from the original tests): results are then opened
    with plain `open`, i.e. the system default browser, which may differ
    from the frontmost one."""
    app = _run([
        "osascript", "-e",
        'tell application "System Events" to name of first application process '
        "whose frontmost is true",
    ])
    script = _FRONT_URL_SCRIPTS.get(app)
    if script is None:
        raise BrowserError(f"the frontmost app ({app or 'unknown'}) is not a known browser")
    return _run(["osascript", "-e", script])


def search_here(query: str, new_tab: bool = False) -> str:
    """Search the site the current tab is on, via SEARCH_SITES.

    The site comes from the tab's URL, never from speech; the query is a
    literal span, URL-encoded into a reviewed template. With new_tab, the
    result opens beside the current tab (Google when the page is not a
    searchable site); otherwise an unknown site is refused.
    """
    q = (query or "").strip().rstrip(_STT_TRAILING).strip()
    if not q:
        raise BrowserError("search query is empty")
    backend = _get_backend()
    reachable = True
    try:
        current = backend.get_current_url()
    except BrowserUnreachable:
        current = _front_browser_url()
        reachable = False
    site = site_for_url(current)

    if new_tab:
        site = site or "google"
        url = search_url(site, q)
        if reachable:
            backend.new_tab(url)
        else:
            _run(["open", url])
        return f"searched {site} for {q!r} in a new tab"

    if site is None:
        raise BrowserError(
            f"{current or 'this page'} is not a searchable site "
            f"({', '.join(SEARCH_SITES)})"
        )
    url = search_url(site, q)
    if reachable:
        backend.navigate(url)
    else:
        _run(["open", url])
    return f"searched {site} for {q!r}"


def open_new_tab(browser: str = "default") -> str:
    """Open an empty tab: in Friday's browser, else in the named app."""
    try:
        _get_backend().new_tab("about:blank")
        return "opened a new tab"
    except BrowserUnreachable:
        app = TAB_BROWSERS.get(browser)
        if app is None:
            raise
        _run(["open", "-a", app, "about:blank"])
        return f"opened a new tab in {app}"


def close_current_tab() -> str:
    """Close the tab pick_target() acts on. Destructive: always confirmed."""
    url = _get_backend().close_tab()
    return f"closed the tab {url}" if url else "closed the current tab"
