"""Connector performance budgets (`@pytest.mark.perf`, NFR performance)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from agentling import AgentSession
from perf_slack import budget

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.mcp_tool import McpTool, RemoteToolSpec, ToolCallOutcome
from aldwin.core.connectors.policy import connector_policy
from aldwin.core.policy import (
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
    register_runtime_policy,
    unregister_runtime_policy,
    wrap_tools_with_policy,
)
from aldwin.core.workspace import Workspace

pytestmark = pytest.mark.perf


class _NoCall:
    async def call_tool(self, *_a: object, **_k: object) -> ToolCallOutcome:
        return ToolCallOutcome()


async def _yes(_req: ApprovalRequest) -> ApprovalAnswer:
    return ApprovalAnswer("yes", "user")


def test_add_tools_mid_turn_under_5ms(
    agent_session: AgentSession, workspace: Workspace, aldwin_home: Path
) -> None:
    """Adding a connector's tools to a live session costs under 5 ms."""
    config = ConnectorConfig(name="fixture", url="https://fixture.example/mcp")
    specs = [
        RemoteToolSpec(
            name=f"tool_{i}",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            read_only_hint=i % 2 == 0,
        )
        for i in range(20)
    ]
    tools = [
        McpTool(connector=config, spec=s, connection=_NoCall(), tmp_dir=aldwin_home)
        for s in specs
    ]
    policies = [connector_policy(config, s) for s in specs]
    session = PolicySession(workspace=workspace, home=aldwin_home)
    try:
        started = time.perf_counter()
        for policy in policies:
            assert policy is not None
            register_runtime_policy(policy)
        wrapped = wrap_tools_with_policy(tools, session=session, ask=_yes, join=True)
        agent_session.add_tools(wrapped, replace=True)
        elapsed = time.perf_counter() - started
    finally:
        for policy in policies:
            assert policy is not None
            unregister_runtime_policy(policy.name)
    assert elapsed < budget(0.005), f"add_tools took {elapsed * 1000:.1f} ms"


def test_mcp_not_imported_at_startup() -> None:
    """Building the host never imports `mcp` (about 220 ms); connections do, later."""
    code = (
        "import sys, aldwin.host.runner, aldwin.host.connectors, aldwin.cli.main; "
        "print(any(m == 'mcp' or m.startswith('mcp.') for m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"
