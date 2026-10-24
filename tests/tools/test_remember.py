"""Named module for `remember` (full behaviour in `test_memory_tools`)."""

from __future__ import annotations

from pathlib import Path

from aldwin.core.policy import REGISTRY, remember_tier
from aldwin.core.tools.memory_tools import RememberTool, running_summary


def test_remember_registry_and_dynamic_tier() -> None:
    """`remember` uses `remember_tier` and Diff when `replace` is set."""
    pol = REGISTRY["remember"]
    assert pol.tier is remember_tier
    assert pol.never_grant == "memory edits"
    assert pol.parallel_safe is False
    assert remember_tier({"text": "hi"}) == "auto"
    assert remember_tier({"text": "hi", "replace": "n1"}) == "diff"


def test_remember_running_summary() -> None:
    """Running summary is empty for new notes and `Remember` on replace."""
    assert running_summary("remember", {"text": "hi"}) == ""
    assert running_summary("remember", {"text": "hi", "replace": "n1"}) == "Remember"
    tool = RememberTool(home=Path("/tmp"))
    assert tool.name == "remember"
    assert "replace" in tool.parameters["properties"]
