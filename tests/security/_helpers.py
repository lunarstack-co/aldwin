"""Shared helpers for security tests."""

from __future__ import annotations

from typing import Any

import pytest
from agentling.tools import ToolCallError


async def call_fail(tool: Any, args: dict[str, Any]) -> str:
    """Run a wrapped tool that must soft-fail; return the model-visible text."""
    with pytest.raises(ToolCallError) as caught:
        await tool.call(args)
    return str(caught.value)
