"""Click classification — PLAN-M6 §5.2.

A single `ax_click` can be a harmless sidebar click or the Send button.
If the tool had a fixed `navigate` class, a planner (or injected
instruction) could press Send or Delete at `navigate` confidence. So the
class is computed at gate time by `classify_click(element, app)` from a
*fresh* observation — never trusted from the planner.

Fails towards the stricter class: empty labels, foreign labels and
lookalike labels are at least `mutate_local`, never `navigate`. The
classifier can only raise a class above the tool's floor, never lower it.
"""

from __future__ import annotations

import re
import unicodedata

from friday.ax import BLOCKED_APPS
from friday.policy import ActionClass

# Roles where a click is navigation — moving around, not changing anything.
NAVIGATION_ROLES = frozenset(
    {
        "outline",
        "outline_row",
        "row",
        "sidebar_item",
        "tab",
        "tab_group",
        "menu_bar_item",
        "menu_item",
        "disclosure_triangle",
        "cell",
    }
)

# Seeded from the labels actually recorded in M6-0 (Finder, Mail, system
# menus), not from guesses. Each entry has a row in
# tests/fixtures/ax/click_cases.json.
OUTWARD_WORDS = (
    "send",
    "submit",
    "post",
    "publish",
    "share",
    "pay",
    "buy",
    "purchase",
    "order",
    "confirm purchase",
    "transfer",
    "sign in with",
)

DESTRUCTIVE_WORDS = (
    "delete",
    "remove",
    "erase",
    "empty trash",
    "move to trash",
    "discard",
    "reset",
    "uninstall",
    "format",
    "sign out",
)

# Privacy panes are blocked wherever they appear, whatever the app.
BLOCKED_LABELS = ("privacy & security", "privacy and security")

_ZERO_WIDTH_BIDI = set("\u200b\u200c\u200d\ufeff\u202a\u202b\u202c\u202d\u202e")


def _is_cyrillic(ch: str) -> bool:
    return "\u0400" <= ch <= "\u04ff"


def _is_greek(ch: str) -> bool:
    return "\u0370" <= ch <= "\u03ff"


def _is_latin(ch: str) -> bool:
    return ch.isascii() and ch.isalpha()


def normalize_label(label: str) -> str:
    """NFKC, case-folded, zero-width/bidi controls removed, ws collapsed."""
    text = unicodedata.normalize("NFKC", label or "")
    text = "".join(ch for ch in text if ch not in _ZERO_WIDTH_BIDI)
    text = text.casefold()
    return " ".join(text.split())


def had_hidden_characters(label: str) -> bool:
    """Zero-width or bidirectional controls were present pre-normalisation."""
    return any(ch in _ZERO_WIDTH_BIDI for ch in (label or ""))


def is_lookalike(label: str) -> bool:
    """A label that mixes scripts or hides non-ASCII in an ASCII UI.

    "Seпd" (Cyrillic п inside a Latin word) must never be `navigate`:
    it is at least `mutate_local`, and the confirmation shows the label's
    code points so the user can see what is really being pressed.
    """
    text = label or ""
    if had_hidden_characters(text):
        return True
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    non_ascii = [ch for ch in letters if ord(ch) > 127]
    if not non_ascii:
        return False
    ascii_letters = [ch for ch in letters if ord(ch) <= 127]
    if ascii_letters:
        # Non-ASCII letters in an otherwise ASCII UI.
        return True
    scripts = set()
    for ch in letters:
        if _is_latin(ch):
            scripts.add("latin")
        elif _is_cyrillic(ch):
            scripts.add("cyrillic")
        elif _is_greek(ch):
            scripts.add("greek")
        else:
            scripts.add("other")
    return len(scripts) > 1


def _phrase_matches(normalized: str, phrase: str) -> bool:
    if " " in phrase:
        return phrase in normalized
    return re.search(r"\b" + re.escape(phrase) + r"\b", normalized) is not None


def classify_click(element, app: str | dict | None = None) -> ActionClass:
    """Action class for clicking `element` in `app`.

    `element` is an ax.Element (or anything with `.role`/`.label`);
    `app` is a name, bundle id, or {"name":…, "bundle_id":…} dict.
    """
    role = getattr(element, "role", "") or ""
    label = getattr(element, "label", "") or ""
    if isinstance(app, dict):
        app_name = app.get("name", "")
        app_id = app.get("bundle_id", "")
    else:
        app_name = app or ""
        app_id = ""
    if app_name in BLOCKED_APPS or app_id in BLOCKED_APPS:
        return ActionClass.BLOCKED
    if role == "secure_field":
        return ActionClass.BLOCKED
    normalized = normalize_label(label)
    if any(b in normalized for b in BLOCKED_LABELS):
        return ActionClass.BLOCKED
    for phrase in DESTRUCTIVE_WORDS:
        if _phrase_matches(normalized, phrase):
            return ActionClass.DESTRUCTIVE
    for phrase in OUTWARD_WORDS:
        if _phrase_matches(normalized, phrase):
            return ActionClass.MUTATE_REMOTE
    if not normalized:
        # Empty label: fail stricter, never navigate.
        return ActionClass.MUTATE_LOCAL
    if is_lookalike(label):
        return ActionClass.MUTATE_LOCAL
    if any(ord(ch) > 127 and ch.isalpha() for ch in normalized):
        # Foreign-language label: the risk lists are English, so a
        # non-navigation control falls to confirm tier, never lower.
        if role not in NAVIGATION_ROLES:
            return ActionClass.MUTATE_LOCAL
        # Even a navigation-role foreign label is not trusted blindly
        # unless it is plainly a file/name-like row — rows are content,
        # not controls. Anything else confirms.
        if role != "row":
            return ActionClass.MUTATE_LOCAL
        return ActionClass.NAVIGATE
    if role in NAVIGATION_ROLES:
        return ActionClass.NAVIGATE
    return ActionClass.MUTATE_LOCAL


def raise_floor(floor: ActionClass, computed: ActionClass) -> ActionClass:
    """The classifier can raise a class above the tool's floor, never lower.

    Order: read < navigate < mutate_local < mutate_remote < destructive;
    blocked always wins (refuse).
    """
    if computed is ActionClass.BLOCKED or floor is ActionClass.BLOCKED:
        return ActionClass.BLOCKED
    order = [
        ActionClass.READ,
        ActionClass.NAVIGATE,
        ActionClass.MUTATE_LOCAL,
        ActionClass.MUTATE_REMOTE,
        ActionClass.DESTRUCTIVE,
    ]
    return order[max(order.index(floor), order.index(computed))]


def codepoints_for_display(label: str) -> str:
    """Code-point rendering for lookalike confirmations.

    The user approves exactly what will be pressed — "Seпd" shows as
    `S U+0053, e U+0065, п U+043F, d U+0064`.
    """
    return ", ".join(f"{ch} U+{ord(ch):04X}" for ch in (label or ""))
