"""REGISTRY tiers and summary helpers for tools that lacked pins."""

from __future__ import annotations

from aldwin.core.policy import REGISTRY
from aldwin.core.tools.calculate import format_row_summary
from aldwin.core.tools.todo import format_todo_echo


def test_todo_and_calculate_registry_tiers() -> None:
    """`todo` and `calculate` are Auto; writers stay Diff/Ask as pinned."""
    assert REGISTRY["todo"].tier == "auto"
    assert REGISTRY["todo"].parallel_safe is False
    assert REGISTRY["calculate"].tier == "auto"
    assert REGISTRY["calculate"].parallel_safe is True
    assert REGISTRY["edit_file"].tier == "diff"
    assert REGISTRY["write_file"].tier == "diff"
    assert REGISTRY["download_file"].tier == "ask"
    assert REGISTRY["create_skill"].tier == "diff"
    assert REGISTRY["forget"].tier == "diff"


def test_todo_echo_and_calculate_summary_shapes() -> None:
    """Row helpers used by `tool_result.summary` stay stable."""
    echo = format_todo_echo(
        [
            {"text": "a", "status": "done"},
            {"text": "b", "status": "in_progress"},
            {"text": "c", "status": "pending"},
        ]
    )
    assert echo.startswith("Tasks 1/3")
    assert "[x] a" in echo
    assert "[>] b" in echo
    assert format_row_summary("15% of 100 = 15") == "Calculate 15% of 100 = 15"
