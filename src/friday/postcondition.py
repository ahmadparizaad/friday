"""Declarative postconditions — PLAN-M6 §M6-4.

A `PlanStep.expect` is a small check evaluated against the *next*
Observation. Forms:

  {"element_appears": {"label": "Message sent", "role": "static_text"}}
  {"element_absent": {"label": "invoice-2026-09.pdf"}}
  {"app_frontmost": {"name": "Mail"}}
  {"window_title_contains": {"text": "New Message"}}
  {"element_value": {"label": "To:", "contains": "bob@example.com"}}
  {"vlm_judged": {"question": "…"}}  — M7; evaluates "not available" in M6

UIs update asynchronously, so `wait_for` polls a fresh observation up to
a short bound (2 s at 200 ms). Timeout means *not satisfied*, never
*assumed satisfied*. Success is decided by these checks, never by planner
text.
"""

from __future__ import annotations

import time
from typing import Callable


class PostconditionError(Exception):
    """The postcondition form is unknown or malformed."""


def check(expect: dict, observation) -> bool:
    """Evaluate one postcondition against one observation. Pure."""
    if not isinstance(expect, dict) or len(expect) != 1:
        raise PostconditionError(f"postcondition must be a single-key object: {expect!r}")
    form, params = next(iter(expect.items()))
    if not isinstance(params, dict):
        raise PostconditionError(f"postcondition {form!r} params must be an object")

    elements = list(getattr(observation, "elements", []) or [])

    if form == "element_appears":
        label = params.get("label", "")
        role = params.get("role")
        if not isinstance(label, str) or not label:
            raise PostconditionError("element_appears needs a non-empty label")
        needle = label.casefold()
        return any(
            needle in (e.label or "").casefold()
            and (role is None or e.role == role)
            for e in elements
        )
    if form == "element_absent":
        label = params.get("label", "")
        if not isinstance(label, str) or not label:
            raise PostconditionError("element_absent needs a non-empty label")
        needle = label.casefold()
        return all(needle not in (e.label or "").casefold() for e in elements)
    if form == "app_frontmost":
        name = params.get("name", "")
        if not isinstance(name, str) or not name:
            raise PostconditionError("app_frontmost needs a non-empty name")
        app = getattr(observation, "app", {}) or {}
        return app.get("name") == name or app.get("bundle_id") == name
    if form == "window_title_contains":
        text = params.get("text", "")
        if not isinstance(text, str) or not text:
            raise PostconditionError("window_title_contains needs non-empty text")
        title = getattr(observation, "window_title", None) or ""
        return text.casefold() in title.casefold()
    if form == "element_value":
        label = params.get("label", "")
        if not isinstance(label, str) or not label:
            raise PostconditionError("element_value needs a non-empty label")
        if "contains" in params and "equals" in params:
            raise PostconditionError("element_value takes contains xor equals")
        needle = params.get("contains", params.get("equals"))
        if not isinstance(needle, str):
            raise PostconditionError("element_value needs a contains/equals string")
        for e in elements:
            if label.casefold() in (e.label or "").casefold():
                value = e.value or ""
                if "equals" in params:
                    if value == needle:
                        return True
                elif needle.casefold() in value.casefold():
                    return True
        return False
    if form == "vlm_judged":
        # M7 implements the VLM judge. M6 evaluates it as not available —
        # never satisfied, so no goal can pass on a VLM check alone yet.
        return False
    raise PostconditionError(f"unknown postcondition form {form!r}")


def wait_for(
    expect: dict,
    snapshot_fn: Callable,
    timeout: float = 2.0,
    interval: float = 0.2,
) -> bool:
    """Poll fresh observations until the check holds or time runs out."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            obs = snapshot_fn()
        except Exception:
            return False
        try:
            if check(expect, obs):
                return True
        except PostconditionError:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
