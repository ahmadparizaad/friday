"""Tool registry tests — ARCHITECTURE.md §5.2, §5.6 (R3).

The registry is the *entire* space of things the agent can do. These
tests are about that space staying narrow.

The registry is now loaded from tools.json rather than a Python tuple
literal (macbrow's pattern) — REGISTRY's shape and every consumer
(agent.py, router.py, slots.py, executor.py) is unchanged, only the
source of truth moved. Widening the tool set is still meant to be a
deliberate, reviewed act: it now means editing tools.json (and adding
any script/module a new argument type needs), not a Python diff, but
the same review bar applies.
"""

import pytest

from friday.policy import ActionClass
from friday.registry import REGISTRY, Tool


def test_registry_is_exactly_the_reviewed_tools():
    """If this fails, someone widened the agent's reach — which should be
    a deliberate, reviewed act, not a side effect.

    open_app and focus_app were merged into use_app: the difference
    between them is whether the app is already running, which is machine
    state the executor can look at and the model cannot. Asking the
    model to guess it caused every routing error measured on the dev
    corpus (docs/ROUTING.md)."""
    assert set(REGISTRY) == {
        "use_app",
        "speak_time",
        "open_url",
        "set_volume",
        "take_screenshot",
        "toggle_dark_mode",
        "quit_app",
        "lock_screen",
        "browser_snapshot",
        "browser_read",
        "browser_click",
        "browser_submit",
        "browser_search",
        "run_skill",
        "navigate_tab",
        "close_tab",
    }


def test_every_tool_declares_an_action_class():
    for name, tool in REGISTRY.items():
        assert isinstance(tool.action_class, ActionClass), name


def test_no_tool_is_open_ended():
    """No run_shell, no arbitrary AppleScript, no fetch_url (R3)."""
    forbidden = {"shell", "exec", "run_script", "applescript", "fetch", "eval"}
    for name in REGISTRY:
        assert not any(f in name.lower() for f in forbidden), name


def test_no_tool_is_never_allow_without_a_declared_class():
    """Every action class actually in use is a real policy class — this
    catches a typo'd action_class string in tools.json at test time
    rather than only at import time."""
    for name, tool in REGISTRY.items():
        assert tool.action_class in set(ActionClass), name


def test_every_tool_has_a_description_for_the_choice():
    """laya routes on these strings; a blank one is unroutable."""
    for name, tool in REGISTRY.items():
        assert tool.description.strip(), name


def test_descriptions_are_phrased_as_questions():
    """The router asks one noul per tool, so each description IS the
    yes/no question laya answers."""
    for name, tool in REGISTRY.items():
        assert tool.description.strip().endswith("?"), name


def test_tool_arguments_are_typed_and_enumerated():
    """Arguments are constrained, never free-form (§5.3)."""
    for name, tool in REGISTRY.items():
        for arg, spec in tool.arguments.items():
            assert spec.get("type") in {
                "enum", "installed_app", "url_shortcut", "literal_span",

                "control_index", "skill_ref", "skill_slots",
            }, (name, arg)
            if spec["type"] == "enum":
                assert spec.get("values"), (name, arg)


def test_use_app_resolves_against_installed_apps():
    """The app name cannot be arbitrary: it is resolved against a scan of
    the machine, so a transcript can never name an app that is not
    installed. The list is bigger than the old hardcoded six; the
    guarantee is the same."""
    from friday.registry import known_apps

    spec = REGISTRY["use_app"].arguments["app"]
    assert spec["type"] == "installed_app"
    assert "Safari" in known_apps()


def test_speak_time_takes_no_arguments():
    assert REGISTRY["speak_time"].arguments == {}


def test_open_url_resolves_against_the_shortcut_table():
    """The URL is never free-spoken: it comes from a closed, code-reviewed
    mapping (shortcuts.py), the same trust tier as an installed app."""
    from friday.shortcuts import SHORTCUTS

    assert REGISTRY["open_url"].arguments["url"]["type"] == "url_shortcut"
    assert REGISTRY["open_url"].arguments["browser"]["type"] == "url_shortcut"
    assert "email" in SHORTCUTS


def test_open_url_is_navigate_class():
    assert REGISTRY["open_url"].action_class == ActionClass.NAVIGATE


def test_set_volume_is_a_closed_enum_not_a_free_number():
    """§1: no argument is ever generated. A spoken number would be an
    unbounded literal_span; the level is a closed set instead."""
    spec = REGISTRY["set_volume"].arguments["level"]
    assert spec["type"] == "enum"
    assert set(spec["values"]) == {"mute", "low", "medium", "high", "max"}


def test_set_volume_is_mutate_local():
    """Changes system state; confirms regardless of confidence (§5.4)."""
    assert REGISTRY["set_volume"].action_class == ActionClass.MUTATE_LOCAL


def test_quit_app_is_mutate_local():
    """Can discard unsaved in-app state, unlike opening/focusing one."""
    assert REGISTRY["quit_app"].action_class == ActionClass.MUTATE_LOCAL


def test_quit_app_resolves_against_installed_apps():
    from friday.registry import known_apps

    spec = REGISTRY["quit_app"].arguments["app"]
    assert spec["type"] == "installed_app"
    assert "Safari" in known_apps()


def test_lock_screen_is_navigate_and_takes_no_arguments():
    """Reversible by unlocking; same tier as opening an app."""
    assert REGISTRY["lock_screen"].action_class == ActionClass.NAVIGATE
    assert REGISTRY["lock_screen"].arguments == {}


def test_toggle_dark_mode_is_mutate_local_and_takes_no_arguments():
    assert REGISTRY["toggle_dark_mode"].action_class == ActionClass.MUTATE_LOCAL
    assert REGISTRY["toggle_dark_mode"].arguments == {}


def test_take_screenshot_is_read_and_takes_no_arguments():
    assert REGISTRY["take_screenshot"].action_class == ActionClass.READ
    assert REGISTRY["take_screenshot"].arguments == {}


def test_take_screenshot_uses_a_binary_not_a_script():
    """screencapture is a system binary, not AppleScript — a distinct
    allow-list from `script`, but still argv-only (§5.6)."""
    tool = REGISTRY["take_screenshot"]
    assert tool.script is None
    assert tool.binary == "screencapture"


def test_only_take_screenshot_declares_a_binary():
    """The binary allow-list is meant to stay as narrow as the script
    allow-list — widening it is a reviewed act, not a side effect."""
    for name, tool in REGISTRY.items():
        if name != "take_screenshot":
            assert tool.binary is None, name


def test_registry_is_immutable():
    with pytest.raises(Exception):
        REGISTRY["evil"] = Tool(
            name="evil",
            description="x",
            action_class=ActionClass.BLOCKED,
            arguments={},
            script="x.applescript",
        )


def test_every_scripted_tool_declares_exactly_one_of_script_or_binary():
    for name, tool in REGISTRY.items():
        assert not (tool.script and tool.binary), name


# --- loader validation (tools.json is well-formed today, so these
# exercise _load_tool/_load_registry directly against crafted bad
# entries — a malformed tools.json should fail at import/load time,
# never load a half-valid tool) --------------------------------------


_VALID_DESCRIPTION = "Is the user asking for x?"


def test_loader_rejects_missing_name():
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool({"description": _VALID_DESCRIPTION, "action_class": "read"})


def test_loader_rejects_missing_description():
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool({"name": "x", "action_class": "read"})


def test_loader_rejects_blank_description():
    """Not cosmetic: the router asks one noul per tool using this exact
    string (docs/ROUTING.md) — a blank one is silently unroutable."""
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool({"name": "x", "description": "   ", "action_class": "read"})


def test_loader_rejects_unknown_action_class():
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool(
            {
                "name": "x",
                "description": _VALID_DESCRIPTION,
                "action_class": "not_a_real_class",
            }
        )


def test_loader_rejects_non_dict_arguments():
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool(
            {
                "name": "x",
                "description": _VALID_DESCRIPTION,
                "action_class": "read",
                "arguments": ["nope"],
            }
        )


def test_loader_rejects_unknown_argument_type():
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool(
            {
                "name": "x",
                "description": _VALID_DESCRIPTION,
                "action_class": "read",
                "arguments": {"a": {"type": "free_text"}},
            }
        )


def test_loader_rejects_enum_with_no_values():
    """A malformed enum must fail here, at load time, with a ValueError —
    not surface later as an unhandled TypeError deep in slots.py's
    _match_enum or executor.py's _validate."""
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool(
            {
                "name": "x",
                "description": _VALID_DESCRIPTION,
                "action_class": "read",
                "arguments": {"a": {"type": "enum"}},
            }
        )

    with pytest.raises(ValueError):
        _load_tool(
            {
                "name": "x",
                "description": _VALID_DESCRIPTION,
                "action_class": "read",
                "arguments": {"a": {"type": "enum", "values": []}},
            }
        )


@pytest.mark.parametrize("bad_values", [None, "ab", {"k": "v"}, 5, True])
def test_loader_rejects_enum_values_that_are_not_a_list(bad_values):
    """A truthy non-list `values` (a string or dict) is falls-through-safe
    for `not spec.get("values")`, but is still a malformed enum: slots.py's
    `tuple(values)` would iterate a string's characters or a dict's keys
    instead of raising, and executor.py's `value in values` would become
    substring containment on a string instead of an allow-list check —
    both wrong silently rather than loud. Caught here so it never reaches
    either."""
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool(
            {
                "name": "x",
                "description": _VALID_DESCRIPTION,
                "action_class": "read",
                "arguments": {"a": {"type": "enum", "values": bad_values}},
            }
        )


def test_loader_rejects_a_blank_script():
    """Mirrors the description check: an empty-string script is falsy for
    the script/binary conflict check but would otherwise load as
    tool.script == "", where SCRIPT_DIR / "" resolves to SCRIPT_DIR
    itself (a directory, so .exists() is True) and execute() would reach
    subprocess.run with a directory as the script path instead of failing
    at load time."""
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool(
            {
                "name": "x",
                "description": _VALID_DESCRIPTION,
                "action_class": "read",
                "script": "",
            }
        )


def test_loader_rejects_both_script_and_binary():
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool(
            {
                "name": "x",
                "description": _VALID_DESCRIPTION,
                "action_class": "read",
                "script": "x.applescript",
                "binary": "screencapture",
            }
        )


def test_loader_rejects_a_binary_outside_the_allow_list():
    """Mirrors the action_class check: a bad binary now fails at load
    time, same as a bad action_class, rather than only being caught if
    the tool is ever actually invoked."""
    from friday.registry import _load_tool

    with pytest.raises(ValueError):
        _load_tool(
            {
                "name": "x",
                "description": _VALID_DESCRIPTION,
                "action_class": "read",
                "binary": "rm",
            }
        )


def test_loader_accepts_a_well_formed_entry():
    from friday.registry import _load_tool

    tool = _load_tool(
        {
            "name": "x",
            "description": "Is the user asking for x?",
            "action_class": "read",
            "arguments": {},
        }
    )
    assert tool.name == "x"
    assert tool.action_class == ActionClass.READ
    assert tool.script is None
    assert tool.binary is None


def test_load_registry_rejects_a_non_array_json_document(tmp_path):
    from friday.registry import _load_registry

    path = tmp_path / "tools.json"
    path.write_text('{"not": "an array"}')
    with pytest.raises(ValueError):
        _load_registry(path)


def test_load_registry_rejects_duplicate_tool_names(tmp_path):
    from friday.registry import _load_registry

    path = tmp_path / "tools.json"
    path.write_text(
        '[{"name": "x", "description": "Is the user asking for x?", '
        '"action_class": "read", "arguments": {}}, '
        '{"name": "x", "description": "Is the user asking for x again?", '
        '"action_class": "navigate", "arguments": {}}]'
    )
    with pytest.raises(ValueError):
        _load_registry(path)


def test_load_registry_loads_a_well_formed_file(tmp_path):
    from friday.registry import _load_registry

    path = tmp_path / "tools.json"
    path.write_text(
        '[{"name": "x", "description": "Is the user asking for x?", '
        '"action_class": "read", "arguments": {}}]'
    )
    registry = _load_registry(path)
    assert set(registry) == {"x"}
    assert registry["x"].action_class == ActionClass.READ


def test_run_skill_is_not_declared_read():
    """The agent applies each skill's own class, but any consumer that
    falls back to the registry must not treat an arbitrary skill as READ."""
    from friday.policy import ActionClass

    assert REGISTRY["run_skill"].action_class == ActionClass.MUTATE_LOCAL
