"""The prompt history file never keeps a line holding a pasted token."""

from __future__ import annotations

from pathlib import Path

from aldwin.cli.chat_screen import _create_history

TOKEN = "ghp_" + "a1B2" * 9


def test_token_lines_are_not_written(tmp_path: Path) -> None:
    history = _create_history(tmp_path)

    history.append_string(f"my token is {TOKEN}")
    history.append_string("what's on my calendar?")

    assert TOKEN not in (tmp_path / "history").read_text("utf-8")
    assert history.get_strings() == [
        f"my token is {TOKEN}",
        "what's on my calendar?",
    ]
    assert list(_create_history(tmp_path).load_history_strings()) == [
        "what's on my calendar?"
    ]
