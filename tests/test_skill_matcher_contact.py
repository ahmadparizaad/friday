"""Contact slots resolve to one email address through the matcher."""

import pytest

from friday.skills.manifest import Skill, Slot
from friday.skills.matcher import MatchError, Resolvers, match
from friday.skills.resolvers import ResolverError


class FakeContacts:
    def __init__(self, book):
        self.book = book

    def resolve(self, text):
        if text.lower() not in self.book:
            raise ResolverError(f"no contact {text!r}")
        return self.book[text.lower()]


SKILL = Skill(
    id="test.mail_someone",
    version="0.1.0",
    title="mail someone",
    triggers=(r"^mail {who}$",),
    slots={"who": Slot(name="who", type="contact", provenance="resolver")},
)


def _resolvers():
    return Resolvers(contact=FakeContacts({"sarah": "sarah@example.com"}))


def test_contact_slot_is_resolved_to_an_address():
    assert match("mail Sarah", (SKILL,), _resolvers()) == (
        "test.mail_someone",
        {"who": "sarah@example.com"},
    )


def test_unknown_contact_refuses_instead_of_passing_the_name_through():
    with pytest.raises(MatchError):
        match("mail nobody", (SKILL,), _resolvers())


def test_trailing_speech_punctuation_is_not_part_of_the_name():
    # speech-to-text ends sentences with "."; "Sarah." is still Sarah
    assert match("mail Sarah.", (SKILL,), _resolvers())[1] == {"who": "sarah@example.com"}
