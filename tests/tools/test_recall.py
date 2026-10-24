"""Named module for `recall` (see `test_memory_tools` / `test_memory_index`)."""

from __future__ import annotations

from pathlib import Path

from aldwin.core.policy import REGISTRY
from aldwin.core.tools.memory_tools import RecallTool, running_summary


def test_recall_registry_and_running_summary() -> None:
    """`recall` is Auto and has a stable running-summary verb."""
    assert REGISTRY["recall"].tier == "auto"
    assert REGISTRY["recall"].writes is False
    tool = RecallTool(home=Path("/tmp"))
    assert tool.name == "recall"
    assert "Recall" in running_summary("recall", {"query": "address"})


def test_recall_scope_parameter_in_schema() -> None:
    """Schema advertises `scope` for notes / conversations / both."""
    props = RecallTool(home=Path("/tmp")).parameters["properties"]
    assert "scope" in props
    assert "query" in props
