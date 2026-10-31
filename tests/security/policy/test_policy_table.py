"""CI pin for `aldwin.core.policy.REGISTRY`."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agentling import Tool

from aldwin.core.policy import (
    REGISTRY,
    PolicyError,
    Tier,
    ToolPolicy,
    remember_tier,
    verify_registered_tools,
)
from aldwin.core.settings import SecretsConfig
from aldwin.core.tools import builtin_tools

# Literal copy of the policy table.
# Changing any tool's tier, path_args, writes, never_grant, or parallel_safe
# in `core/policy.py` requires updating this table in the same PR.
PINNED_POLICY_TABLE: dict[
    str, tuple[Tier | str, tuple[str, ...], bool, str | None, bool]
] = {
    "web_search": ("auto", (), False, None, True),
    "web_fetch": ("auto", (), False, None, True),
    "recall": ("auto", (), False, None, True),
    "remember": ("callable:remember_tier", (), False, "memory edits", False),
    "todo": ("auto", (), False, None, False),
    "calculate": ("auto", (), False, None, True),
    "list_dir": ("auto", ("path",), False, None, True),
    "glob": ("auto", ("path",), False, None, True),
    "grep": ("auto", ("path",), False, None, True),
    "read_file": ("auto", ("path",), False, None, True),
    "read_document": ("auto", ("path",), False, None, True),
    "edit_file": ("diff", ("path",), True, None, False),
    "write_file": ("diff", ("path",), True, None, False),
    "forget": ("diff", (), False, "memory deletes", False),
    "create_skill": ("diff", (), False, "new skills", False),
    "set_persona": ("diff", (), False, "persona changes", False),
    "run_command": ("ask", ("cwd",), False, "commands", False),
    "download_file": ("ask", ("dest",), True, "downloads", False),
}


def _snapshot_registry(
    reg: dict[str, ToolPolicy],
) -> dict[str, tuple[Tier | str, tuple[str, ...], bool, str | None, bool]]:
    result: dict[str, tuple[Tier | str, tuple[str, ...], bool, str | None, bool]] = {}
    for name, pol in reg.items():
        tier_repr: Tier | str = (
            f"callable:{pol.tier.__name__}" if callable(pol.tier) else pol.tier
        )
        result[name] = (
            tier_repr,
            pol.path_args,
            pol.writes,
            pol.never_grant,
            pol.parallel_safe,
        )
    return result


class _RogueTool(Tool):
    name = "rogue_unregistered_tool"
    description = "Unregistered test tool."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def forward(self) -> str:  # type: ignore[override]
        return "nope"


class _FinalAnswerDummy(Tool):
    name = "final_answer"
    description = "Reserved final_answer tool."
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def forward(self) -> str:  # type: ignore[override]
        return "done"


def test_unregistered_tool_fails_startup() -> None:
    """Unregistered tool fails startup with exact `PolicyError`."""
    verify_registered_tools([_FinalAnswerDummy()])
    with pytest.raises(PolicyError) as exc_info:
        verify_registered_tools([_RogueTool()])
    assert (
        str(exc_info.value)
        == 'tool "rogue_unregistered_tool" has no policy entry in core/policy.py'
    )


def test_policy_registry_matches_pinned_table(aldwin_home: Path) -> None:
    """`REGISTRY` matches the pinned 18-tool table and built tools."""
    assert set(REGISTRY.keys()) == set(PINNED_POLICY_TABLE.keys())
    assert len(REGISTRY) == 18
    assert _snapshot_registry(REGISTRY) == PINNED_POLICY_TABLE

    assert REGISTRY["remember"].tier is remember_tier
    assert remember_tier({"text": "new note"}) == "auto"
    assert remember_tier({"text": "updated", "replace": "id_1"}) == "diff"

    # `TC-77`: built-ins never use the connector-only fields.
    for policy in REGISTRY.values():
        assert policy.connector is None
        assert policy.auto_reason is None
        assert policy.session_grant is False
        assert policy.untrusted_source is None

    # Mutating one tier in a copy of REGISTRY must fail the table comparison
    mutated = dict(REGISTRY)
    mutated["read_file"] = replace(REGISTRY["read_file"], tier="ask")
    assert _snapshot_registry(mutated) != PINNED_POLICY_TABLE

    # Verify all currently built tools in `builtin_tools` have valid entries
    async def _noop_ask(_s: str, _d: str = "") -> bool:
        return False

    built = builtin_tools(_noop_ask, home=aldwin_home, secrets=SecretsConfig())
    verify_registered_tools(built)
    for t in built:
        assert t.name in REGISTRY
        assert t.parallel_safe == REGISTRY[t.name].parallel_safe
