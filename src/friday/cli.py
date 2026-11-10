"""Terminal interface — ARCHITECTURE.md §8 (M1).

M1's interface, and permanently the headless test harness: policy, audit
and the injection corpus run here with no display, no mic and no Swift.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import sys
from pathlib import Path

from friday.agent import DEFAULT_LOG, Agent
from friday.policy import Autonomy
from friday.settings import Settings


def confirm(description: str, verbatim: bool) -> bool:
    """Read back the resolved action and wait for an explicit yes.

    The description is printed in full — no truncation (§9.2). Anything
    that is not an explicit yes is a denial, and so is EOF.
    """
    banner = "CONFIRM (verbatim)" if verbatim else "CONFIRM"
    print(f"\n  {banner}: {description}")
    try:
        answer = input("  approve? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _stop_ui(process) -> None:
    """Terminate the UI process, waiting briefly for it to go."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except Exception:  # noqa: BLE001 - escalate rather than hang on exit
        process.kill()


def _show(supervisor, state: str) -> None:
    """Report state to the UI, tolerating its absence or death.

    A dead UI must degrade, not end the session with a traceback: the
    agent still works, it just has nowhere to draw. Confirmations are a
    different matter — those go through `supervisor.confirm`, which denies
    rather than proceeding unseen.
    """
    if supervisor is None:
        return
    try:
        supervisor.set_state(state)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        print(f"  (ui unavailable: {exc})", file=sys.stderr)


# --- skill CLI commands (S11 subset) ----------------------------------------


_APPLESCRIPT_STUB = "on run argv\n    return item 1 of argv\nend run\n"


def _skill_new(args) -> int:
    import shutil
    from pathlib import Path

    from friday.skills.manifest import SKILL_ID_RE

    if not SKILL_ID_RE.fullmatch(args.id):
        print(
            f"error: invalid skill id {args.id!r}; use lowercase letters, "
            "digits and underscores, dot-separated (e.g. files.find_open)",
            file=sys.stderr,
        )
        return 1
    backend = args.backend
    templates = {
        "cli": {
            "id": args.id,
            "version": "0.1.0",
            "title": args.id.replace(".", " ").title(),
            "triggers": [f"do {args.id.split('.')[-1]} {{name}}"],
            "slots": {"name": {"type": "text", "provenance": "utterance", "max_len": 120}},
            "action_class": "navigate",
            "requires": {"binaries": ["mdfind"], "apps": [], "permissions": []},
            "allowed_dirs": ["~/Downloads", "~/Documents", "~/Desktop"],
            "steps": [
                {
                    "id": "act",
                    "backend": "cli",
                    "argv": ["mdfind", "-name", "{name}"],
                    "dry_run": f"search for {{name}}",
                }
            ],
            "postcondition": {"type": "output_matches", "value": ".*"},
            "undo": "none",
        },
        "applescript": {
            "id": args.id,
            "version": "0.1.0",
            "title": args.id.replace(".", " ").title(),
            "triggers": [f"do {args.id.split('.')[-1]} {{name}}"],
            "slots": {"name": {"type": "text", "provenance": "utterance", "max_len": 120}},
            "action_class": "navigate",
            "requires": {"binaries": [], "apps": ["Finder"], "permissions": ["Automation"]},
            "allowed_dirs": ["~/Downloads", "~/Documents", "~/Desktop"],
            "steps": [
                {
                    "id": "act",
                    "backend": "applescript",
                    "script": f"{args.id.split('.')[-1]}.applescript",
                    "argv": ["{name}"],
                    "dry_run": f"run applescript for {{name}}",
                }
            ],
            "postcondition": {"type": "output_matches", "value": ".*"},
            "undo": "none",
        },
        "url": {
            "id": args.id,
            "version": "0.1.0",
            "title": args.id.replace(".", " ").title(),
            "triggers": [f"open {args.id.split('.')[-1]} {{url}}"],
            "slots": {"url": {"type": "text", "provenance": "utterance", "max_len": 500}},
            "action_class": "navigate",
            "requires": {"binaries": [], "apps": [], "permissions": []},
            "allowed_dirs": [],
            "steps": [
                {
                    "id": "act",
                    "backend": "url",
                    "argv": ["{url}"],
                    "dry_run": "open {url}",
                }
            ],
            "postcondition": {"type": "output_matches", "value": ".*"},
            "undo": "none",
        },
        "browser": {
            "id": args.id,
            "version": "0.1.0",
            "title": args.id.replace(".", " ").title(),
            "triggers": [f"search {args.id.split('.')[-1]} {{query}}"],
            "slots": {"query": {"type": "text", "provenance": "utterance", "max_len": 200}},
            "action_class": "navigate",
            "requires": {"binaries": [], "apps": [], "permissions": ["github.com"]},
            "allowed_dirs": [],
            "steps": [
                {
                    "id": "go",
                    "backend": "browser",
                    "argv": ["navigate_to_site", "github.com", "/search?q={query}"],
                    "dry_run": "search github for {query}",
                }
            ],
            "postcondition": {"type": "output_matches", "value": "github.com"},
            "undo": "none",
        },
    }
    if backend not in templates:
        print(f"unknown backend {backend!r}; choose from {sorted(templates)}", file=sys.stderr)
        return 1
    skill_dir = Path.cwd() / "skills" / args.id
    try:
        skill_dir.mkdir(parents=True)
    except FileExistsError:
        print(f"error: {skill_dir} already exists; not overwriting", file=sys.stderr)
        return 1
    try:
        if backend == "applescript":
            script = templates[backend]["steps"][0]["script"]
            (skill_dir / script).write_text(_APPLESCRIPT_STUB)
        (skill_dir / "skill.json").write_text(
            json.dumps(templates[backend], indent=2)
        )
        (skill_dir / "examples.jsonl").write_text(
            f'{{"u": "do {args.id.split(".")[-1]} example"}}\n'
        )
        (skill_dir / "tests").mkdir(exist_ok=True)
    except OSError as exc:
        shutil.rmtree(skill_dir, ignore_errors=True)
        print(f"error: could not create {skill_dir}: {exc}", file=sys.stderr)
        return 1
    print(f"created {skill_dir}")
    return 0


def _browser_launch(args) -> int:
    from friday.browser_launcher import launch_browser

    try:
        proc = launch_browser(
            profile_dir=args.profile,
            debug_port=args.port,
            extra_args=args.args,
            detach=args.detach,
        )
        if args.detach:
            print(f"Browser launched (PID: {proc.pid})", file=sys.stderr)
        else:
            print(f"Browser launched in foreground (PID: {proc.pid})", file=sys.stderr)
            proc.wait()
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _browser_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="friday browser")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("launch", help="launch Friday browser profile")
    p.add_argument("--port", type=int, default=9222, help="Debug port (default: 9222)")
    p.add_argument("--profile", type=Path, help="Profile directory")
    p.add_argument("--args", nargs="*", default=[], help="Extra browser arguments")
    p.add_argument("--detach", action="store_true", help="Run detached (background)")
    p.set_defaults(func=_browser_launch)

    args = parser.parse_args(argv)
    return args.func(args)


def _skill_validate(args) -> int:
    from pathlib import Path

    from friday.skills.manifest import ManifestError, load_manifest
    from friday.skills.validate import validate_manifest

    skill_dir = Path(args.dir)
    try:
        skill = load_manifest(skill_dir / "skill.json")
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    errors = validate_manifest(skill)
    if args.json:
        print(json.dumps(errors))
    else:
        if errors:
            print("errors:")
            for e in errors:
                print(f"  {e}")
        else:
            print("valid")
    return 1 if errors else 0


def _skill_test(args) -> int:
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "pytest", args.dir, "-q"],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def _skill_dry_run(args) -> int:
    from pathlib import Path

    from friday.skills.manifest import ManifestError, load_manifest
    from friday.skills.runtime import run_skill

    skill_dir = Path(args.dir)
    try:
        skill = load_manifest(skill_dir / "skill.json")
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    slots = {}
    for s in args.slots:
        if "=" not in s:
            print(f"slot {s!r} must be key=value", file=sys.stderr)
            return 1
        k, v = s.split("=", 1)
        slots[k] = v
    result = run_skill(skill, slots, dry_run=True)
    if result.ok:
        print(result.output)
    else:
        print(f"error: {result.output}", file=sys.stderr)
        return 1
    return 0


def _skill_enable(args) -> int:
    from friday.skills.store import StoreError, enable

    try:
        enable(args.id)
        print(f"enabled {args.id}")
        return 0
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _skill_disable(args) -> int:
    from friday.skills.store import StoreError, disable

    try:
        disable(args.id)
        print(f"disabled {args.id}")
        return 0
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _skill_list(args) -> int:
    from friday.skills.store import discover, enabled_skills

    all_skills = discover()
    enabled_ids = {s.id for s in enabled_skills()}
    if not all_skills:
        print("no skills found")
        return 0
    for skill in all_skills:
        status = "enabled" if skill.id in enabled_ids else "disabled"
        print(f"  {skill.id} v{skill.version} [{status}] — {skill.title}")
    return 0


def _skill_cli(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="friday skill")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="scaffold a new skill")
    p.add_argument("id")
    p.add_argument("--backend", choices=["cli", "applescript", "url", "browser"], default="cli")
    p.set_defaults(func=_skill_new)

    p = sub.add_parser("validate", help="validate a skill")
    p.add_argument("dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_skill_validate)

    p = sub.add_parser("test", help="run a skill's tests")
    p.add_argument("dir")
    p.set_defaults(func=_skill_test)

    p = sub.add_parser("dry-run", help="show what a skill would do")
    p.add_argument("dir")
    p.add_argument("--slots", nargs="+", default=[])
    p.set_defaults(func=_skill_dry_run)

    p = sub.add_parser("enable", help="enable a skill")
    p.add_argument("id")
    p.set_defaults(func=_skill_enable)

    p = sub.add_parser("disable", help="disable a skill")
    p.add_argument("id")
    p.set_defaults(func=_skill_disable)

    p = sub.add_parser("list", help="list all skills")
    p.set_defaults(func=_skill_list)

    args = parser.parse_args(argv)
    return args.func(args)


def main(argv=None) -> int:
    # Handle skill subcommands first (they don't need the agent loop)
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["skill"]:
        return _skill_cli(argv[1:])
    if argv[:1] == ["browser"]:
        return _browser_cli(argv[1:])

    parser = argparse.ArgumentParser(prog="friday")
    parser.add_argument(
        "--autonomy",
        choices=[a.value for a in Autonomy],
        default=None,
        help="override the persisted autonomy level for this run only",
    )
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    parser.add_argument(
        "--retain-days",
        type=int,
        default=None,
        help="override the persisted audit retention (days) for this run",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="confirm in the notch UI instead of the terminal (§9)",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="listen via the mic (hold fn) instead of reading stdin; implies --ui",
    )
    parser.add_argument(
        "--hosted",
        action="store_true",
        help="hosted mode: Swift parent, use stdin/stdout channel; implies --voice",
    )
    args = parser.parse_args(argv)
    if args.hosted:
        args.voice = True
        args.ui = False
    elif args.voice:
        args.ui = True

    from friday.router import Router

    settings = Settings.load()

    supervisor = None
    voice = None
    confirm_with = confirm
    ui_pipes = None
    ui_process = None
    if args.hosted:
        import os
        from friday.hosted import (
            claim_stdout,
            install_sigterm_exit,
            stdio_channel,
            watch_parent,
        )

        os.setpgrp()
        install_sigterm_exit()
        watch_parent(interval=1.0)
        channel_fd, _ = claim_stdout()
        channel_write, channel_read = stdio_channel(in_fd=0, out_fd=channel_fd)
        ui_pipes = (channel_write, channel_read, None)
        ui_process = None
        channel_write(json.dumps({"t": "settings", "settings": settings.to_dict()}))
    elif args.ui:
        from friday.supervisor import spawn, spawn_pipes

        if args.voice:
            ui_pipes = spawn_pipes()
            ui_process = ui_pipes[2]
        else:
            supervisor, ui_process = spawn()
            confirm_with = supervisor.confirm

        atexit.register(_stop_ui, ui_process)

        if ui_pipes is not None:
            write, _, _ = ui_pipes
            write(json.dumps({"t": "settings", "settings": settings.to_dict()}) + "\n")

    print("loading model…", file=sys.stderr)
    # Precedence (M3): the persisted Settings level wins at startup; an
    # explicit --autonomy flag overrides it for this run only and is not
    # written back, so a one-off --autonomy strict never surprises the
    # next launch.
    autonomy = (
        Autonomy(args.autonomy)
        if args.autonomy is not None
        else Autonomy(settings.autonomy)
    )
    # M4 retention precedence: explicit --retain-days wins for this
    # run only (and is not written back); otherwise the persisted
    # Settings value applies. Expired records prune at startup.
    from friday.audit import enforce_retention
    from friday.settings import _coerce_retention_days

    retain_days = (
        _coerce_retention_days(args.retain_days)
        if args.retain_days is not None
        else settings.audit_retention_days
    )
    try:
        enforce_retention(args.log, retain_days)
    except Exception as exc:  # noqa: BLE001 - report, never block launch
        print(f"  (retention: {exc})", file=sys.stderr)
    agent = Agent(
        router=Router(),
        confirm=confirm_with,
        autonomy=autonomy,
        log_path=args.log,
    )
    if ui_pipes is not None:
        # Voice mode replaces the stdin loop entirely.
        from friday.voice import VoiceLoop

        write, read, _process = ui_pipes
        voice = VoiceLoop(
            agent=agent,
            write=write,
            read=read,
            on_outcome=lambda text, outcome: print(
                f"> {text}\n  [{outcome.status}] {outcome.message}"
            ),
        )
        agent.set_confirm(voice.confirm)

    surface = "notch (hosted)" if args.hosted else ("notch" if args.ui else "terminal")
    source = "mic (hold fn)" if args.voice else "stdin"
    print(
        f"ready — autonomy={autonomy.value}, input={source}, "
        f"confirm={surface}, log={args.log}",
        file=sys.stderr,
    )

    if voice is not None:
        voice.set_state("idle")
        voice.run()
        return 0

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        # No "routing" state: routing measures ~80ms (docs/ROUTING.md), so
        # a spinner would flash for a few frames and read as noise. This
        # answers ARCHITECTURE.md open question 6.
        _show(supervisor, "executing")
        outcome = agent.handle(line)
        _show(supervisor, "failed" if outcome.status == "failed" else "idle")
        print(f"  [{outcome.status}] {outcome.message}")


if __name__ == "__main__":
    raise SystemExit(main())
