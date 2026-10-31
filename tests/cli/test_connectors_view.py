"""`/connectors` view, connector tool rows and `/tools` (`FR-25`, `FR-26`, `FR-28`)."""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from aldwin.cli.commands import handle_slash_command
from aldwin.cli.connectors_view import handle_connectors_command
from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import create_console
from aldwin.client import ProtocolError
from aldwin.protocol import (
    ApprovalEvent,
    ConnectorInfo,
    ConnectorProbeEvent,
    ConnectorsEvent,
    ConnectorStatusEvent,
    ConnectorToolInfo,
    ToolCallEvent,
    ToolResultEvent,
)

READ = ConnectorToolInfo(
    name="read_thing",
    model_name="mcp__fixture__read_thing",
    read_only=True,
    effective="auto",
)
WRITE = ConnectorToolInfo(
    name="write_thing", model_name="mcp__fixture__write_thing", effective="ask"
)


def _info(**kw: Any) -> ConnectorInfo:
    base: dict[str, Any] = {
        "name": "fixture",
        "title": "Fixture",
        "url": "https://fixture.example/mcp",
        "status": "connected",
        "tools": [READ, WRITE],
    }
    base.update(kw)
    return ConnectorInfo(**base)


class FakeConnectorsClient:
    """Answers like the host; records what the view asked for."""

    def __init__(self, items: list[ConnectorInfo] | None = None) -> None:
        self.items = items or []
        self.error: str | None = None
        self.calls: list[tuple[Any, ...]] = []
        self.needs_token = False
        self.add_error: ProtocolError | None = None

    def tools_summary(self) -> list[Any]:
        return []

    async def connectors_list(self) -> ConnectorsEvent:
        return ConnectorsEvent(re="r", items=self.items, error=self.error)

    async def connectors_add(self, **kw: Any) -> Any:
        self.calls.append(("add", kw))
        if self.add_error is not None:
            raise self.add_error
        if self.needs_token and not kw.get("token"):
            raise ProtocolError("fixture.example needs a token.", code="auth_required")
        if kw.get("dry_run"):
            return ConnectorProbeEvent(
                re="r",
                name="fixture",
                title="Fixture",
                tools=[READ, WRITE],
                read=1,
                write=1,
            )
        return ConnectorStatusEvent(
            re="r", name=kw["name"], status="connected", tools=2
        )

    async def connectors_enable(self, name: str) -> ConnectorStatusEvent:
        self.calls.append(("enable", name))
        return ConnectorStatusEvent(re="r", name=name, status="connected", tools=2)

    async def connectors_disable(self, name: str) -> ConnectorStatusEvent:
        self.calls.append(("disable", name))
        return ConnectorStatusEvent(re="r", name=name, status="disabled")

    async def connectors_remove(self, name: str) -> None:
        self.calls.append(("remove", name))

    async def connectors_reconnect(self, name: str) -> None:
        self.calls.append(("reconnect", name))

    async def connectors_tool_policy(
        self, name: str, tool: str, setting: str
    ) -> ConnectorsEvent:
        self.calls.append(("tool_policy", name, tool, setting))
        return ConnectorsEvent(re="r", items=self.items)

    async def connectors_trust_hints(self, name: str, trust: bool) -> ConnectorsEvent:
        self.calls.append(("trust_hints", name, trust))
        return ConnectorsEvent(re="r", items=self.items)


def _renderer() -> tuple[ChatRenderer, io.StringIO]:
    buf = io.StringIO()
    console = create_console(plain=True, width=100, record=True, file=buf)
    return ChatRenderer(console, plain=True), buf


def _answers(*lines: str) -> Callable[[str], Awaitable[str]]:
    queue = list(lines)
    prompts: list[str] = []

    async def prompt(label: str) -> str:
        prompts.append(label)
        return queue.pop(0)

    prompt.prompts = prompts  # type: ignore[attr-defined]
    return prompt


async def _run(client: FakeConnectorsClient, *lines: str, secret: str = "") -> str:
    renderer, buf = _renderer()

    async def secret_prompt(_label: str) -> str:
        return secret

    await handle_connectors_command(
        client=client,
        renderer=renderer,
        prompt_line=_answers(*lines),
        prompt_secret=secret_prompt,
    )
    return buf.getvalue()


async def test_list_shows_glyphs_summary_add_and_config_error() -> None:
    client = FakeConnectorsClient(
        [
            _info(),
            _info(
                name="gh",
                title="GitHub",
                status="failed",
                error="Couldn't reach x: down.",
                retry_in_s=30,
            ),
            _info(name="old", title="Old", status="disabled", enabled=False),
        ]
    )
    client.error = "connectors.yaml: connectors.x.enabled must be a boolean"
    out = await _run(client, "")
    assert "connectors.yaml: connectors.x.enabled must be a boolean" in out
    assert "● Fixture  2 tools · reads auto, writes ask" in out
    assert "✕ GitHub  failed: Couldn't reach x: down. (retrying in 30s)" in out
    assert "○ Old  disabled" in out
    assert "+ Add a connector…" in out


async def test_add_flow_happy_path() -> None:
    client = FakeConnectorsClient()
    out = await _run(client, "1", "https://fixture.example/mcp", "", "1")
    assert (
        "Fixture has 2 tools: 1 read (run automatically), 1 write (ask first)." in out
    )
    assert "Fixture connected. 2 tools are available now." in out
    probe, save = client.calls
    assert probe[1]["dry_run"] is True
    assert save[1] == {
        "url": "https://fixture.example/mcp",
        "name": "fixture",
        "auth": "none",
        "token": None,
    }


async def test_add_flow_asks_for_a_token_on_401() -> None:
    client = FakeConnectorsClient()
    client.needs_token = True
    out = await _run(
        client,
        "1",
        "https://fixture.example/mcp",
        "2",
        "mine",
        "1",
        secret="canary-7f3a",
    )
    assert "fixture.example needs a token." in out
    assert "Mine connected." in out
    save = client.calls[-1][1]
    assert (save["name"], save["auth"], save["token"]) == (
        "mine",
        "bearer",
        "canary-7f3a",
    )
    assert "canary-7f3a" not in out


@pytest.mark.parametrize(
    "message",
    [
        "That address isn't an MCP server (got text/html).",
        "Couldn't reach fixture.example: All connection attempts failed.",
        "The server refused the token (401).",
        "Connector URL must use https:// (got http://x)",
    ],
)
async def test_add_flow_shows_each_error_and_saves_nothing(message: str) -> None:
    """`TC-12` (door half)."""
    client = FakeConnectorsClient()
    client.add_error = ProtocolError(message, code="connector")
    out = await _run(client, "1", "https://fixture.example/mcp")
    assert message in out
    assert len(client.calls) == 1 and client.calls[0][1]["dry_run"] is True


async def test_manage_actions() -> None:
    client = FakeConnectorsClient([_info()])
    await _run(client, "1", "3")  # Disable
    await _run(client, "1", "2")  # Reconnect
    await _run(client, "1", "5")  # Trust hints off
    out = await _run(client, "1", "4", "2")  # Remove, Yes
    assert "Remove Fixture and its saved token?" in out
    await _run(client, "1", "1", "2", "3")  # Tools -> write_thing -> off
    assert client.calls == [
        ("disable", "fixture"),
        ("reconnect", "fixture"),
        ("trust_hints", "fixture", False),
        ("remove", "fixture"),
        ("tool_policy", "fixture", "write_thing", "off"),
    ]


async def test_mcp_is_an_alias_and_tools_lists_connectors() -> None:
    client = FakeConnectorsClient([_info()])
    renderer, buf = _renderer()
    for cmd in ("/connectors", "/mcp"):
        await handle_slash_command(
            cmd,
            client=client,  # type: ignore[arg-type]
            renderer=renderer,
            prompt_line=_answers(""),
        )
    assert buf.getvalue().count("+ Add a connector…") == 2


def test_tool_row_shows_connector_and_reason() -> None:
    renderer, buf = _renderer()
    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(
            re="r",
            call_id="c1",
            name="mcp__context7__resolve-library-id",
            args_preview='{"libraryName": "next.js"}',
            connector="context7",
            connector_title="Context7",
            remote_tool="resolve-library-id",
            reason="server says read-only",
        )
    )
    turn.on_tool_result(ToolResultEvent(re="r", call_id="c1", ok=True, summary="found"))
    turn.close()
    out = buf.getvalue()
    assert "Context7 · resolve-library-id" in out
    assert "auto · server says read-only" in out


def test_tool_grant_reads_yes_for_this_session() -> None:
    renderer, _buf = _renderer()
    event = ApprovalEvent(
        re="r",
        approval_id="a",
        summary="GitHub wants to run create_issue",
        grantable=True,
        grant_scope="tool",
        risky=["write", "external"],
    )
    options = renderer._approval_chooser_options(event).plain
    assert "Yes for this session" in options


async def test_server_tool_names_are_sanitised() -> None:
    sneaky = ConnectorToolInfo(
        name="read‮evil", model_name="mcp__fixture__read_evil", effective="ask"
    )
    client = FakeConnectorsClient([_info(tools=[sneaky])])
    out = await _run(client, "1", "1", "")  # open Fixture -> Tools, then cancel
    assert "‮" not in out
    assert "readevil" in out

    renderer, buf = _renderer()
    await handle_slash_command(
        "/tools",
        client=client,  # type: ignore[arg-type]
        renderer=renderer,
        prompt_line=_answers(),
    )
    assert "readevil" in buf.getvalue() and "‮" not in buf.getvalue()
