"""`McpConnection` and the add-flow probe (`FR-5`, `FR-26` errors)."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fixtures.mcp_server import (
    LONG_TOOL,
    PNG_BLOCK,
    PNG_BYTES,
    FixtureControls,
    serve_http,
)

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.connection import (
    BearerAuth,
    McpConnection,
    _outcome,
    build_auth,
    probe_connector,
)
from aldwin.core.connectors.credentials import FileCredentialStore, bearer_credential
from aldwin.core.connectors.errors import (
    ConnectorAuthRejected,
    ConnectorAuthRequired,
    ConnectorError,
    ConnectorNotMcp,
    ConnectorSessionLost,
    ConnectorUnreachable,
)
from aldwin.core.tools import USER_AGENT

FIXTURE_TOOLS = {
    "read_thing",
    "write_thing",
    LONG_TOOL,
    "echo_nullable",
    "fail_thing",
}


def _config(url: str = "https://fixture.example/mcp", **kw: Any) -> ConnectorConfig:
    return ConnectorConfig(name="fixture", url=url, **kw)


class _Events:
    def __init__(self) -> None:
        self.changed = asyncio.Event()
        self.lost: list[ConnectorError] = []
        self.lost_event = asyncio.Event()

    async def on_tools_changed(self) -> None:
        self.changed.set()

    async def on_lost(self, error: ConnectorError) -> None:
        self.lost.append(error)
        self.lost_event.set()


def _in_process(
    controls: FixtureControls, events: _Events, mode: str = "auto"
) -> McpConnection:
    return McpConnection(
        _config(),
        auth=None,
        on_tools_changed=events.on_tools_changed,
        on_lost=events.on_lost,
        transport_factory=lambda _http: controls.server,
        protocol_mode=mode,
    )


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_open_lists_tools_and_list_changed_fires(
    fixture_server: FixtureControls, mode: str
) -> None:
    events = _Events()
    conn = _in_process(fixture_server, events, mode)
    try:
        specs = await conn.open()
        assert {s.name for s in specs} == FIXTURE_TOOLS
        read = next(s for s in specs if s.name == "read_thing")
        assert read.read_only_hint is True
        assert read.description == "Read something harmless."
        write = next(s for s in specs if s.name == "write_thing")
        assert write.read_only_hint is None
        assert write.input_schema["required"] == ["value"]

        await fixture_server.swap_tools()
        await asyncio.wait_for(events.changed.wait(), 3)
        names = {s.name for s in await conn.list_tools()}
        assert "new_thing" in names and "write_thing" not in names
    finally:
        await conn.close()
    assert events.lost == []


async def test_call_tool_outcomes(fixture_server: FixtureControls) -> None:
    """`TC-07` (transport half): `null` reaches the server untouched."""
    conn = _in_process(fixture_server, _Events())
    try:
        await conn.open()
        nullable = await conn.call_tool("echo_nullable", {"value": None})
        assert (nullable.is_error, nullable.text_parts) == (False, ["None"])

        failed = await conn.call_tool("fail_thing", {})
        assert failed.is_error is True
        assert "bad input" in failed.error_text

        fixture_server.content("read_thing", [PNG_BLOCK])
        picture = await conn.call_tool("read_thing", {})
        assert picture.binary_parts == [("image/png", PNG_BYTES)]
    finally:
        await conn.close()


async def test_calls_after_close_say_not_connected(
    fixture_server: FixtureControls,
) -> None:
    conn = _in_process(fixture_server, _Events())
    await conn.open()
    await conn.close()
    await conn.close()
    with pytest.raises(ConnectorSessionLost, match="Fixture is not connected"):
        await conn.call_tool("read_thing", {})


async def test_probe_lists_and_disconnects(fixture_server: FixtureControls) -> None:
    specs = await probe_connector(
        _config(), None, transport_factory=lambda _http: fixture_server.server
    )
    assert {s.name for s in specs} == FIXTURE_TOOLS


def test_build_auth_hides_the_token(aldwin_home: Any) -> None:
    store = FileCredentialStore(aldwin_home)
    store.set("fixture", bearer_credential("secret-token"))
    bearer = _config(auth="bearer")
    auth = build_auth(bearer, store, {})
    assert auth is not None
    assert "secret-token" not in repr(auth)
    assert build_auth(_config(), store, {}) is None


# --- over HTTP --------------------------------------------------------------


@pytest.mark.loopback
async def test_http_sends_bearer_and_user_agent(
    fixture_server: FixtureControls, fixture_url: str
) -> None:
    fixture_server.require_bearer("tok-123")
    specs = await probe_connector(_config(f"{fixture_url}/mcp"), BearerAuth("tok-123"))
    assert {s.name for s in specs} == FIXTURE_TOOLS
    first = fixture_server.requests[0]
    assert first["authorization"] == "Bearer tok-123"
    assert first["user-agent"] == USER_AGENT


@pytest.mark.loopback
async def test_http_401_without_token_needs_one(fixture_url: str) -> None:
    host = fixture_url.removeprefix("http://")
    with pytest.raises(ConnectorAuthRequired) as info:
        await probe_connector(_config(f"{fixture_url}/401"), None)
    assert info.value.message == f"{host} needs a token."


@pytest.mark.loopback
async def test_http_401_with_token_is_refused(fixture_url: str) -> None:
    with pytest.raises(ConnectorAuthRejected) as info:
        await probe_connector(_config(f"{fixture_url}/401"), BearerAuth("wrong"))
    assert info.value.message == "The server refused the token (401)."


@pytest.mark.loopback
async def test_http_html_is_not_mcp(fixture_url: str) -> None:
    with pytest.raises(ConnectorNotMcp) as info:
        await probe_connector(_config(f"{fixture_url}/html"), None)
    assert info.value.message == "That address isn't an MCP server (got text/html)."


@pytest.mark.loopback
async def test_closed_port_is_unreachable() -> None:
    with pytest.raises(ConnectorUnreachable) as info:
        await probe_connector(_config("http://127.0.0.1:1/mcp"), None)
    assert info.value.message.startswith("Couldn't reach 127.0.0.1:1: ")
    assert info.value.message.endswith(".")


@pytest.mark.loopback
async def test_server_stopping_reports_lost(fixture_server: FixtureControls) -> None:
    events = _Events()
    async with serve_http(fixture_server) as url:
        conn = McpConnection(
            _config(f"{url}/mcp"),
            auth=None,
            on_tools_changed=events.on_tools_changed,
            on_lost=events.on_lost,
        )
        await conn.open()
    try:
        await asyncio.wait_for(events.lost_event.wait(), 10)
        assert isinstance(events.lost[0], ConnectorSessionLost)
        with pytest.raises(ConnectorSessionLost):
            await conn.call_tool("read_thing", {})
    finally:
        await conn.close()


# --- failure paths ----------------------------------------------------------


async def test_open_times_out_and_abandons_a_hung_connect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @asynccontextmanager
    async def _never_connects() -> AsyncIterator[Any]:
        await asyncio.Event().wait()
        yield None

    conn = McpConnection(
        _config(),
        auth=None,
        on_tools_changed=_Events().on_tools_changed,
        on_lost=_Events().on_lost,
        transport_factory=lambda _http: _never_connects(),
    )
    with pytest.raises(ConnectorUnreachable) as info:
        await conn.open(timeout=0.2)
    assert info.value.message == "Couldn't reach fixture.example: no answer after 0.2s."
    assert "abandoned" in caplog.text


async def test_failed_call_marks_the_session_lost(
    fixture_server: FixtureControls, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = _Events()
    conn = _in_process(fixture_server, events)
    await conn.open()

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("socket closed")

    monkeypatch.setattr(conn._client, "call_tool", _boom)
    with pytest.raises(ConnectorSessionLost) as info:
        await conn.call_tool("read_thing", {})
    assert info.value.message == "Fixture disconnected; try again shortly."
    await asyncio.wait_for(events.lost_event.wait(), 3)
    assert events.lost == [info.value]
    with pytest.raises(ConnectorSessionLost):
        await conn.list_tools()
    await conn.close()


async def test_transport_error_message_marks_the_session_lost(
    fixture_server: FixtureControls,
) -> None:
    events = _Events()
    conn = _in_process(fixture_server, events)
    await conn.open()
    await conn._on_message(RuntimeError("stream broke"))
    await asyncio.wait_for(events.lost_event.wait(), 3)
    assert isinstance(events.lost[0], ConnectorSessionLost)
    await conn.close()


def test_classify_session_terminated_and_passthrough() -> None:
    from mcp.shared.exceptions import MCPError

    conn = McpConnection(
        _config(),
        auth=None,
        on_tools_changed=_Events().on_tools_changed,
        on_lost=_Events().on_lost,
    )
    lost = conn._classify(ExceptionGroup("g", [MCPError(-32600, "Session terminated")]))
    assert isinstance(lost, ConnectorSessionLost)
    own = ConnectorNotMcp("text/plain")
    assert conn._classify(ExceptionGroup("g", [own])) is own


def test_outcome_renders_every_content_kind() -> None:
    from mcp_types import (
        BlobResourceContents,
        CallToolResult,
        EmbeddedResource,
        ResourceLink,
        TextResourceContents,
    )

    blob = base64.b64encode(b"\x00\x01").decode()
    result = CallToolResult(
        content=[
            ResourceLink(type="resource_link", uri="https://x.example/doc", name="doc"),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(uri="file:///a.txt", text="inline text"),
            ),
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(uri="file:///b.bin", blob=blob),
            ),
        ]
    )
    out = _outcome(result)
    assert out.text_parts == ["resource: https://x.example/doc", "inline text"]
    assert out.binary_parts == [("application/octet-stream", b"\x00\x01")]

    structured = _outcome(CallToolResult(content=[], structured_content={"n": 1}))
    assert structured.text_parts == ['{"n": 1}']


@pytest.mark.loopback
async def test_session_expiry_reports_lost(
    fixture_server: FixtureControls, fixture_url: str
) -> None:
    """A `404 Session terminated` mid-session is a lost session, not a tool error.

    Only 2025-era sessions have an id to expire; 2026-07-28 HTTP is stateless.
    """
    events = _Events()
    conn = McpConnection(
        _config(f"{fixture_url}/mcp"),
        auth=None,
        on_tools_changed=events.on_tools_changed,
        on_lost=events.on_lost,
        protocol_mode="legacy",
    )
    await conn.open()
    try:
        fixture_server.expire_session()
        with pytest.raises(ConnectorSessionLost) as info:
            await conn.call_tool("read_thing", {})
        assert info.value.message == "Fixture disconnected; try again shortly."
        await asyncio.wait_for(events.lost_event.wait(), 5)
    finally:
        await conn.close()
