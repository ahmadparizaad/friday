"""Resolver tests — file confinement and the contact resolver."""

import os
import stat

import pytest

from friday.skills.resolvers import ContactResolver, FileResolver, ResolverError


def _fake_mdfind(tmp_path, *paths):
    script = tmp_path / "fake_mdfind"
    script.write_text("#!/bin/sh\n" + "".join(f'echo "{p}"\n' for p in paths))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def test_file_resolver_refuses_a_symlink_that_escapes_allowed_dirs(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    link = ok / "link.txt"
    link.symlink_to(secret)
    resolver = FileResolver((str(ok),), binary=_fake_mdfind(tmp_path, link))
    with pytest.raises(ResolverError, match="outside"):
        resolver.resolve("link")


def test_file_resolver_refuses_a_match_outside_allowed_dirs(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    resolver = FileResolver((str(ok),), binary=_fake_mdfind(tmp_path, "/etc/hosts"))
    with pytest.raises(ResolverError, match="outside"):
        resolver.resolve("hosts")


def test_file_resolver_returns_a_match_inside_allowed_dirs(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    f = ok / "a.pdf"
    f.write_text("x")
    resolver = FileResolver((str(ok),), binary=_fake_mdfind(tmp_path, f))
    assert resolver.resolve("a") == str(f)


def _contacts(*rows):
    return lambda name: list(rows)


def test_contact_resolves_to_a_single_email():
    r = ContactResolver(_contacts(("John Smith", "john@example.com")))
    assert r.resolve("john") == "john@example.com"


def test_contact_with_no_match_raises():
    with pytest.raises(ResolverError, match="no contact"):
        ContactResolver(_contacts()).resolve("zed")


def test_ambiguous_contact_raises_and_names_the_candidates():
    r = ContactResolver(
        _contacts(("John Smith", "js@example.com"), ("John Doe", "jd@example.com"))
    )
    with pytest.raises(ResolverError, match="John Smith.*John Doe|John Doe.*John Smith"):
        r.resolve("john")


def test_exact_full_name_beats_partial_matches():
    r = ContactResolver(
        _contacts(("John Smith", "js@example.com"), ("John Smithers", "jsr@example.com"))
    )
    assert r.resolve("john smith") == "js@example.com"


def test_one_contact_with_two_addresses_is_ambiguous():
    r = ContactResolver(
        _contacts(("Ann Lee", "ann@home.com"), ("Ann Lee", "ann@work.com"))
    )
    with pytest.raises(ResolverError, match="ambiguous"):
        r.resolve("ann lee")


def test_a_literal_email_address_passes_through_without_a_lookup():
    def boom(name):
        raise AssertionError("must not look up an email address")

    assert ContactResolver(boom).resolve("a.b@example.com") == "a.b@example.com"


def test_lookup_failure_becomes_a_resolver_error():
    def failing(name):
        raise OSError("Contacts not authorised")

    with pytest.raises(ResolverError, match="contacts"):
        ContactResolver(failing).resolve("john")


# --- review fixes -----------------------------------------------------------


def test_a_partial_name_inside_another_word_is_not_a_match():
    """'Ann' must not resolve to 'Joanna Smith'."""
    r = ContactResolver(_contacts(("Joanna Smith", "joanna@example.com")))
    with pytest.raises(ResolverError, match="no contact") as exc:
        r.resolve("ann")
    assert "Joanna Smith" in str(exc.value)  # offered as a hint, not chosen


def test_a_whole_word_of_the_name_is_a_match():
    r = ContactResolver(_contacts(("Ann Lee", "ann@example.com")))
    assert r.resolve("ann") == "ann@example.com"
    assert r.resolve("LEE") == "ann@example.com"


def test_one_person_with_two_addresses_says_so():
    r = ContactResolver(_contacts(("Ann Lee", "ann@home.com"), ("Ann Lee", "ann@work.com")))
    with pytest.raises(ResolverError, match="more than one address"):
        r.resolve("ann lee")


def test_lookup_parses_unit_separated_rows_and_drops_bad_emails(monkeypatch):
    import subprocess

    from friday.skills import resolvers

    out = "Ann Lee\x1fann@example.com\nBad\x1f-oops\nNoSep\nTab\tName\x1ft@example.com\n"
    monkeypatch.setattr(
        resolvers.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""),
    )
    assert resolvers._contacts_lookup("x") == [
        ("Ann Lee", "ann@example.com"),
        ("Tab\tName", "t@example.com"),
    ]


def test_lookup_timeout_hints_at_automation_permission():
    import subprocess

    def slow(name):
        raise subprocess.TimeoutExpired("osascript", 10)

    with pytest.raises(ResolverError, match="Automation"):
        ContactResolver(slow).resolve("ann")


def test_file_resolver_returns_the_resolved_path(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    real = ok / "real.pdf"
    real.write_text("x")
    link = ok / "link.pdf"
    link.symlink_to(real)
    resolver = FileResolver((str(ok),), binary=_fake_mdfind(tmp_path, link))
    assert resolver.resolve("link") == str(real.resolve())


# --- bare app names (a skill slot captures "Safari", not "open Safari") ----------

_APPS = ("Safari", "Notes", "Google Chrome", "Google Chrome Canary", "Visual Studio Code")


@pytest.mark.parametrize(
    "spoken, app",
    [("Safari", "Safari"), ("notes", "Notes"), ("the Notes", "Notes"),
     ("google chrome", "Google Chrome"), ("visual studio code", "Visual Studio Code"),
     ("code", "Visual Studio Code")],
)
def test_app_resolver_takes_a_bare_name(spoken, app):
    from friday.skills.resolvers import AppResolver

    assert AppResolver(_APPS).resolve(spoken) == app


@pytest.mark.parametrize("spoken", ["chrome", "photoshop", "safari and notes", ""])
def test_app_resolver_refuses_ambiguous_unknown_or_several(spoken):
    from friday.skills.resolvers import AppResolver, ResolverError

    with pytest.raises(ResolverError):
        AppResolver(_APPS).resolve(spoken)
