"""Speech path tests — ARCHITECTURE.md §5.1, §7.

Marked `live`: needs the real SpeechAnalyzer and the laya checkpoint.

These use `say` to synthesize speech and feed the audio through the real
transcriber, so the whole voice path is testable without a human talking
into the mic. That matters because the alternative — "it worked when I
tried it" — is not a regression test.

Build first:  swiftc -O ui/*.swift -o build/friday-ui
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

REPO = Path(__file__).parent.parent
PROBE = REPO / "build/transcribe-file"
PROBE_SOURCE = REPO / "ui/probes/TranscribeFile.swift"


def _build_probe():
    if PROBE.exists():
        return True
    if not PROBE_SOURCE.exists():
        return False
    PROBE.parent.mkdir(exist_ok=True)
    result = subprocess.run(
        ["swiftc", "-O", str(PROBE_SOURCE), "-o", str(PROBE)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def transcribe(phrase: str) -> str:
    """Synthesize `phrase`, transcribe it, return what the model heard."""
    if not _build_probe():
        pytest.skip("transcribe-file probe unavailable")

    aiff = Path("/tmp/friday_test.aiff")
    wav = Path("/tmp/friday_test.wav")
    subprocess.run(["say", "-o", str(aiff), phrase], check=True, capture_output=True)
    subprocess.run(
        ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)],
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        [str(PROBE), str(wav)], capture_output=True, text=True, timeout=60
    )
    for line in result.stdout.splitlines():
        if line.startswith("TRANSCRIPT:"):
            return line.split(":", 1)[1].strip()
    pytest.skip(f"no transcript produced: {result.stdout} {result.stderr}")


@pytest.mark.parametrize(
    "phrase,expected_tool",
    [
        ("open notes", "use_app"),
        ("what time is it", "speak_time"),
        ("switch to safari", "use_app"),
    ],
)
def test_spoken_phrases_route_correctly(phrase, expected_tool):
    """End to end: synthesized speech -> transcript -> route.

    The transcriber returns capitalized, punctuated text ("Open notes."),
    which the router and slot grammar must handle — they were built and
    measured against lowercase corpus entries.
    """
    from friday.router import Router

    heard = transcribe(phrase)
    assert heard, "nothing transcribed"

    route = Router().route(heard)
    assert route.tool == expected_tool, f"heard {heard!r} -> {route.tool}"


def test_spoken_arguments_survive_punctuation_and_case():
    """'Open notes.' must still yield the canonical 'Notes'."""
    from friday.router import Router
    from friday.slots import extract

    heard = transcribe("open notes")
    route = Router().route(heard)
    assert extract(route.tool, heard) == {"app": "Notes"}


def test_a_spoken_out_of_scope_request_is_still_refused():
    """The injection/out-of-scope guarantee must hold for speech too, not
    just for typed input."""
    from friday.router import RouteError, Router
    from friday.slots import SlotError, extract

    heard = transcribe("delete all my files")
    assert heard

    try:
        route = Router().route(heard)
    except RouteError:
        return  # refused at the router

    with pytest.raises(SlotError):
        extract(route.tool, heard)
