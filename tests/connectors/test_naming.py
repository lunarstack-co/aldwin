"""Connector tool naming (`FR-7`, `TC-06`)."""

from __future__ import annotations

import hashlib
import re

import pytest

from aldwin.core.connectors.naming import model_tool_name

AGENTLING_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def test_plain_name() -> None:
    assert model_tool_name("github", "create_issue") == "mcp__github__create_issue"


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("resolve.library id", "mcp__ctx__resolve_library_id"),
        ("ünïcode/tool", "mcp__ctx___n_code_tool"),
        ("keep-dash_and_UNDER", "mcp__ctx__keep-dash_and_UNDER"),
    ],
)
def test_unsafe_characters_become_underscores(tool: str, expected: str) -> None:
    assert model_tool_name("ctx", tool) == expected


def test_exactly_64_characters_is_kept() -> None:
    tool = "t" * (64 - len("mcp__fixture__"))
    name = model_tool_name("fixture", tool)
    assert len(name) == 64
    assert name == f"mcp__fixture__{tool}"


def test_long_name_is_clamped_with_hash() -> None:
    """`TC-06`: a 70-character tool becomes 64 characters ending in `_<8 hex>`."""
    tool = "t" * 70
    full = f"mcp__fixture__{tool}"
    name = model_tool_name("fixture", tool)
    assert len(name) == 64
    assert name.startswith("mcp__fixture__")
    assert name == full[:55] + "_" + hashlib.sha256(full.encode()).hexdigest()[:8]


def test_names_are_stable_and_distinct() -> None:
    a = model_tool_name("fixture", "a" * 70)
    assert model_tool_name("fixture", "a" * 70) == a
    assert model_tool_name("fixture", "a" * 71) != a


@pytest.mark.parametrize(
    "tool", ["x", "a.b c", "t" * 70, "ü" * 100, "tool/with:many?chars"]
)
def test_every_name_passes_agentling_check(tool: str) -> None:
    assert AGENTLING_NAME.fullmatch(model_tool_name("conn-1", tool))
