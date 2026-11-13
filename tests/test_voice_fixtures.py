"""Voice eval fixtures must not contain banned filler phrases."""

from __future__ import annotations

import json
from pathlib import Path

from aldwin.cli.voice import BANNED_VOICE_PHRASES, contains_banned_voice

FIXTURES = Path(__file__).parent / "fixtures" / "voice"


def test_banned_list_covers_filler_phrases() -> None:
    """Banned list includes the common assistant filler phrases."""
    required = (
        "let me know if",
        "let's proceed",
        "feel free to ask",
        "if there's anything else",
        "please let me know",
    )
    for phrase in required:
        assert phrase in BANNED_VOICE_PHRASES


def test_five_voice_fixtures_exist_and_are_clean() -> None:
    """Exactly five fixtures; no banned phrases in assistant text."""
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) == 5, f"expected 5 fixtures, found {len(files)}"

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for turn in data.get("turns", []):
            if turn.get("role") != "assistant":
                continue
            text = str(turn.get("text", ""))
            hits = contains_banned_voice(text)
            assert not hits, f"{path.name}: banned {hits!r} in {text!r}"


def test_instructions_include_voice_rules() -> None:
    """Base instructions encode the short-preface and no-filler rules."""
    from importlib.resources import files

    text = (
        files("aldwin.core.assets")
        .joinpath("instructions.md")
        .read_text(encoding="utf-8")
    )
    lower = text.lower()
    assert "at most one short sentence" in lower
    assert "let's proceed" in lower
    # A failure is retried or worked around; a decline is respected, never retried.
    assert "never make the identical call a third time" in lower
    assert "declined approval is different" in lower
    assert "do not retry it" in lower
