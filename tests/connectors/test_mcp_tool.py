"""`McpTool`: naming, schemas, results and approval text (`FR-6`)."""

from __future__ import annotations

import os
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from agentling.tools import ToolCallError, _check_tool
from fixtures.mcp_server import LONG_TOOL, PNG_BYTES, FixtureControls

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.connection import McpConnection
from aldwin.core.connectors.errors import ConnectorError, ConnectorSessionLost
from aldwin.core.connectors.mcp_tool import (
    MAX_DESCRIPTION_CHARS,
    McpTool,
    RemoteToolSpec,
    ToolCallOutcome,
    normalize_schema,
    sanitize_description,
)
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

CONFIG = ConnectorConfig(name="fixture", url="https://fixture.example/mcp")


async def _ignore_lost(_error: ConnectorError) -> None:
    return None


async def _noop() -> None:
    return None


@pytest.fixture
async def connection(fixture_server: FixtureControls) -> AsyncIterator[McpConnection]:
    conn = McpConnection(
        CONFIG,
        auth=None,
        on_tools_changed=_noop,
        on_lost=_ignore_lost,
        transport_factory=lambda _http: fixture_server.server,
    )
    await conn.open()
    yield conn
    await conn.close()


def _tool(connection: Any, tmp: Path, name: str = "read_thing", **spec: Any) -> McpTool:
    return McpTool(
        connector=CONFIG,
        spec=RemoteToolSpec(name=name, **spec),
        connection=connection,
        tmp_dir=tmp,
    )


async def test_null_argument_reaches_the_server(
    connection: McpConnection, tmp_path: Path
) -> None:
    """`TC-07`: `null` for a `["string","null"]` argument is not refused locally."""
    tool = _tool(connection, tmp_path, "echo_nullable")
    assert tool.validate_arguments is False
    assert await tool.forward(value=None) == "None"


async def test_tool_error_raises_with_server_text(
    connection: McpConnection, tmp_path: Path
) -> None:
    with pytest.raises(ToolCallError, match="bad input"):
        await _tool(connection, tmp_path, "fail_thing").forward()


async def test_image_is_saved_privately_and_path_returned(
    connection: McpConnection, aldwin_home: Path
) -> None:
    tmp = aldwin_home / "tmp"
    out = await _tool(connection, tmp, "picture").forward()
    assert out.startswith("saved image/png to ")
    path = Path(out.removeprefix("saved image/png to "))
    assert path.parent == tmp
    assert path.name.startswith("mcp-fixture-") and path.suffix == ".png"
    assert path.read_bytes() == PNG_BYTES
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


async def test_lost_session_becomes_a_tool_error(tmp_path: Path) -> None:
    class _Lost:
        async def call_tool(self, *_a: Any, **_k: Any) -> ToolCallOutcome:
            raise ConnectorSessionLost("Fixture disconnected; try again shortly.")

    with pytest.raises(ToolCallError, match="^Fixture disconnected; try again"):
        await _tool(_Lost(), tmp_path).forward()


async def test_empty_result_says_so(tmp_path: Path) -> None:
    class _Empty:
        async def call_tool(self, *_a: Any, **_k: Any) -> ToolCallOutcome:
            return ToolCallOutcome()

    assert await _tool(_Empty(), tmp_path).forward() == "(no output)"


async def test_wrapped_result_is_untrusted_with_connector_source(
    connection: McpConnection, workspace: Workspace, aldwin_home: Path
) -> None:
    """End to end through the policy wrapper with a real connection."""
    spec = RemoteToolSpec(name="read_thing", read_only_hint=True)
    policy = connector_policy(CONFIG, spec)
    assert policy is not None
    register_runtime_policy(policy)
    try:

        async def _never(_req: ApprovalRequest) -> ApprovalAnswer:
            raise AssertionError("read-only tool must not ask")

        tool = McpTool(
            connector=CONFIG, spec=spec, connection=connection, tmp_dir=aldwin_home
        )
        [wrapped] = wrap_tools_with_policy(
            [tool],
            session=PolicySession(workspace=workspace, home=aldwin_home),
            ask=_never,
        )
        out = await wrapped.call({})
        assert 'source="mcp:fixture:read_thing"' in out
        assert "read ok" in out
    finally:
        unregister_runtime_policy(policy.name)


def test_names_and_schemas_pass_agentling_checks(tmp_path: Path) -> None:
    long_tool = _tool(None, tmp_path, LONG_TOOL)
    assert len(long_tool.name) == 64
    _check_tool(long_tool)
    schemas: list[Any] = [{}, {"type": "string"}, None, {"properties": {"q": {}}}]
    for schema in schemas:
        _check_tool(_tool(None, tmp_path, "x", input_schema=schema))


def test_normalize_schema() -> None:
    obj = {"type": "object", "properties": {"q": {"type": "string"}}}
    assert normalize_schema(obj) is obj
    assert normalize_schema({"properties": {"q": {}}}) == {
        "properties": {"q": {}},
        "type": "object",
    }
    assert normalize_schema({"type": "array"}) == {"type": "object", "properties": {}}
    assert normalize_schema("nope") == {"type": "object", "properties": {}}


def test_sanitize_description() -> None:
    sneaky = "Reads‮ files\x1b[31m​\nsecond line\tok"
    assert sanitize_description(sneaky) == "Reads files[31m\nsecond line\tok"
    assert len(sanitize_description("x" * 5_000)) == MAX_DESCRIPTION_CHARS


def test_prepare_approval_text(tmp_path: Path) -> None:
    titled = _tool(None, tmp_path, "create_issue", title="Create issue")
    preview = titled.prepare_approval(title="Bug", body="x" * 300, labels=["a"])
    assert preview.summary == "Fixture wants to run Create issue"
    assert preview.risky == ("write", "external")
    assert preview.detail is not None
    lines = preview.detail.splitlines()
    assert lines[0] == "body: " + "x" * 199 + "…"
    assert lines[1:] == ["labels: ['a']", "title: Bug"]

    untitled = _tool(None, tmp_path, "create_issue")
    assert untitled.prepare_approval().summary == "Fixture wants to run create_issue"
    assert untitled.prepare_approval().detail is None
