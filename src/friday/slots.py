"""Deterministic slot extraction — ARCHITECTURE.md §5.3.

No model runs here. An argument is either a value from the tool's
declared enum (matched against the transcript) or a literal span of the
transcript. If the grammar does not match, the utterance is refused —
it never falls through to a guess or a default.

The value returned for an enum is the *registry's* canonical spelling,
not the text from the transcript, so a lookalike string cannot be
smuggled through.
"""

from __future__ import annotations

import re

from friday.registry import REGISTRY


class SlotError(Exception):
    """Extraction failed. The caller must refuse, not substitute."""


def _match_enum(transcript: str, values: tuple[str, ...]) -> str:
    """Return the single enum value named in the transcript.

    Zero matches or more than one are both refusals: ambiguity is not
    resolved by picking a favourite.
    """
    found = {
        value
        for value in values
        if re.search(rf"\b{re.escape(value.lower())}\b", transcript)
    }
    if not found:
        raise SlotError(f"no value from {values} found in transcript")
    if len(found) > 1:
        raise SlotError(f"ambiguous: transcript names {sorted(found)}")
    return found.pop()


def extract(tool_name: str, transcript: str) -> dict:
    """Extract this tool's arguments from the transcript, or raise."""
    args, _sources = extract_with_sources(tool_name, transcript)
    return args



ALLOW_LIST = {"source": "allow_list"}
MAX_QUERY = 200

# Spoken site -> browser.SEARCH_SITES key.
_SITE_WORDS = {
    "google": "google",
    "github": "github",
    "youtube": "youtube",
    "wikipedia": "wikipedia",
    "stackoverflow": "stackoverflow",
    "stack overflow": "stackoverflow",
}
_SITE_RE = "|".join(sorted((re.escape(w) for w in _SITE_WORDS), key=len, reverse=True))
_END = r"[\s.!?,;:]*$"
_SEARCH_FORMS = (
    # (pattern, site); site None means "named in the pattern".
    (rf"^(?:open (?:a )?new tab and )search (?:for )?(?P<q>.+?){_END}", "current_new_tab"),
    (rf"^search (?:for )?(?P<q>.+?) in a new tab{_END}", "current_new_tab"),
    (rf"^search (?P<site>{_SITE_RE}) for (?P<q>.+?){_END}", None),
    (rf"^search (?:for )?(?P<q>.+?) on (?P<site>{_SITE_RE}){_END}", None),
    (rf"^search (?:for )?(?P<q>.+?){_END}", "current"),
)

_NUMBER_WORDS = {
    w: i
    for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve "
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}


def _span(start: int, end: int) -> dict:
    return {"source": "transcript", "start": start, "end": end}


def _browser_search(transcript: str) -> tuple[dict, dict]:
    lead = len(transcript) - len(transcript.lstrip())
    text = transcript.strip()
    for pattern, fixed_site in _SEARCH_FORMS:
        m = re.match(pattern, text, flags=re.IGNORECASE)
        if m is None:
            continue
        query = m.group("q").strip()
        start = m.start("q") + (len(m.group("q")) - len(m.group("q").lstrip()))
        site = fixed_site or _SITE_WORDS[m.group("site").lower()]
        if not query or query.lower() == "for":  # "search for" has no query
            raise SlotError("search query is empty")
        if len(query) > MAX_QUERY:
            raise SlotError(f"search query is longer than {MAX_QUERY} characters")
        if fixed_site in ("current", "current_new_tab"):
            # A bare query must not be a mangled site form: it cannot start
            # with a site name ("search github actions"), name a site at the
            # end ("... on youtube" in a new tab), or be "<word> for ..."
            # ("search myspace for x" names an unknown site).
            if re.match(rf"(?:{_SITE_RE})\b", query, flags=re.IGNORECASE):
                raise SlotError("names a site, but not as 'search SITE for QUERY'")
            if re.search(rf"\bon (?:{_SITE_RE})$", query, flags=re.IGNORECASE):
                raise SlotError("a new-tab search cannot also name a site")
            if re.match(r"\S+ for \S", query, flags=re.IGNORECASE):
                raise SlotError("looks like 'search SITE for QUERY' with an unknown site")
        args = {"site": site, "query": query}
        sources = {"site": ALLOW_LIST, "query": _span(lead + start, lead + start + len(query))}
        return args, sources
    raise SlotError("not a search request ('search QUERY' or 'search SITE for QUERY')")


def _control_index(text: str) -> int:
    found = {int(d) for d in re.findall(r"\b\d+\b", text)}
    found |= {n for w, n in _NUMBER_WORDS.items() if re.search(rf"\b{w}\b", text)}
    if not found:
        raise SlotError("no control number named")
    if len(found) > 1:
        raise SlotError(f"ambiguous: transcript names controls {sorted(found)}")
    return found.pop()


def _owning_app(word: str, apps: tuple[str, ...]) -> str | None:
    """An installed app that *is* the named service (WhatsApp for
    "whatsapp", ChatGPT for "chat gpt"), compared without spaces."""
    key = word.replace(" ", "").lower()
    for app in apps:
        if app.replace(" ", "").lower() == key:
            return app
    return None


def _open_url(text: str) -> tuple[dict, dict]:
    from friday.registry import known_apps
    from friday.shortcuts import ShortcutError, resolve_shortcut, wants_web

    try:
        resolved = resolve_shortcut(text)
    except ShortcutError as exc:
        raise SlotError(str(exc)) from exc
    if resolved is None:
        raise SlotError("no known url shortcut named in the request")
    word, url, browser = resolved
    owner = _owning_app(word, known_apps())
    if owner is not None and not wants_web(text):
        raise SlotError(f"{owner} is installed; use_app opens the app, not the web version")
    return {"url": url, "browser": browser}, {"url": ALLOW_LIST, "browser": ALLOW_LIST}


def _installed_app(text: str, tool_name: str) -> str:
    from friday.apps import resolve_app
    from friday.registry import known_apps

    apps = known_apps()
    resolved = resolve_app(text, apps)
    if resolved is None:
        raise SlotError("no installed application named in the request")
    if tool_name == "use_app":
        from friday.shortcuts import ShortcutError, resolve_shortcut, wants_web

        try:
            service = resolve_shortcut(text)
        except ShortcutError as exc:  # several services named: ambiguous, refuse
            raise SlotError(str(exc)) from exc
        if service is not None:
            word = service[0]
            if wants_web(text) or _owning_app(word, (resolved,)) is None:
                # "open spotify on the web", or "open google maps" where
                # "google" only reached Google Chrome: the web service was
                # meant, so open_url owns it.
                raise SlotError(f"{word!r} names a web service; open_url opens it")
    return resolved


def _tab_gate(text: str, verbs: str) -> None:
    if not re.search(rf"\b(?:{verbs})\b", text) or not re.search(r"\btab\b", text):
        raise SlotError("needs a tab verb and the word 'tab'")


def _run_skill(transcript: str) -> tuple[dict, dict]:
    from friday.skills.matcher import MatchError, match
    from friday.skills.store import enabled_skills

    try:
        skill_id, slots = match(transcript, enabled_skills())
    except MatchError as exc:
        raise SlotError(str(exc)) from exc
    return {"skill_id": skill_id, "slots": slots}, {"skill_id": ALLOW_LIST, "slots": ALLOW_LIST}


def extract_with_sources(tool_name: str, transcript: str) -> tuple[dict, dict]:
    """Like extract(), plus where each value came from: the allow-list, or
    a transcript span with offsets into the original `transcript`."""
    tool = REGISTRY.get(tool_name)
    if tool is None:
        raise SlotError(f"unknown tool {tool_name!r}")

    text = transcript.strip().lower()
    if not text:
        raise SlotError("empty transcript")

    if tool_name == "browser_search":
        return _browser_search(transcript)
    if tool_name == "run_skill":
        return _run_skill(transcript)
    if tool_name == "close_tab":
        _tab_gate(text, "close|shut")
        return {}, {}
    if tool_name == "navigate_tab":
        _tab_gate(text, "open|new|create|start")
        if re.search(r"\b(?:close|shut)\b", text):
            raise SlotError("a close request is not a new-tab request")
        values = tuple(v for v in tool.arguments["browser"]["values"] if v != "default")
        named = [v for v in values if re.search(rf"\b{re.escape(v)}\b", text)]
        # brave and its mishearing "brew" are one browser, not two
        named = sorted({"brave" if v == "brew" else v for v in named})
        if len(named) > 1:
            raise SlotError(f"ambiguous: transcript names browsers {named}")
        return {"browser": named[0] if named else "default"}, {"browser": ALLOW_LIST}
    if any(spec["type"] == "url_shortcut" for spec in tool.arguments.values()):
        return _open_url(text)

    args: dict = {}
    sources: dict = {}
    for arg_name, spec in tool.arguments.items():
        kind = spec["type"]
        if kind == "enum":
            args[arg_name] = _match_enum(text, tuple(spec["values"]))
        elif kind == "installed_app":
            args[arg_name] = _installed_app(text, tool_name)
        elif kind == "control_index":
            args[arg_name] = _control_index(text)
        else:
            raise SlotError(f"unsupported slot type {kind!r}")
        sources[arg_name] = ALLOW_LIST
    return args, sources
