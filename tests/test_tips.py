"""Rotating tip helpers for the welcome card."""

from __future__ import annotations

from pathlib import Path

from aldwin.cli.tips import TIPS, next_tip


def test_tips_count_and_themes() -> None:
    """Fifteen tips across core discoverability themes."""
    assert len(TIPS) == 15
    assert "Switch between cloud and local models any time with /model." in TIPS
    joined = " ".join(TIPS).lower()
    for needle in (
        "@",
        "/model",
        "cloud and local",
        "esc",
        "alt+enter",
        "/verbose",
        "find every todo",
        "summarise report.pdf",
        "/connectors",
    ):
        assert needle in joined


def test_next_tip_rotates_without_back_to_back(aldwin_home: Path) -> None:
    """Three launches get three tips; never repeats consecutively."""
    a = next_tip(aldwin_home, plain=False)
    b = next_tip(aldwin_home, plain=False)
    c = next_tip(aldwin_home, plain=False)
    assert a and b and c
    assert a != b
    assert b != c
    assert a in TIPS and b in TIPS and c in TIPS
    state = aldwin_home / "state" / "last_tip.txt"
    assert state.is_file()
    assert state.read_text(encoding="utf-8").strip() == c


def test_plain_mode_returns_none(aldwin_home: Path) -> None:
    """Plain mode shows no tip."""
    assert next_tip(aldwin_home, plain=True) is None
