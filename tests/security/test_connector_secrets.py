"""Connector tokens never leave the credential store (`TC-13`, NFR security)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest
from fakes.model import FakeModel, final, tool_call
from fixtures.mcp_server import build_fixture_server, serve_http

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.connectors.config import credentials_dir
from aldwin.core.policy import PolicySession, decide
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.workspace import Workspace
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import ConnectorStatusEvent

TOKEN = "mcp-canary-9d41c7"


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _files_containing(root: Path, needle: str) -> list[Path]:
    hits = []
    for path in root.rglob("*"):
        if path.is_file() and needle.encode() in path.read_bytes():
            hits.append(path)
    return hits


@pytest.mark.loopback
async def test_token_never_leaves_credentials_store(
    fake_model: FakeModel, aldwin_home: Path
) -> None:
    controls = build_fixture_server()
    controls.require_bearer(TOKEN)
    sent: list[Any] = []

    async with serve_http(controls) as url:
        transport = InProcessTransport()
        host = AldwinHost(
            transport,
            agent_builder=lambda name, ask: build_agent(
                AldwinConfig(model=name),
                SecretsConfig(),
                ask,
                home=aldwin_home,
                model=fake_model,
            ),
        )
        original_send = transport.send_event

        async def record(event: Any) -> None:
            sent.append(event)
            await original_send(event)

        transport.send_event = record  # type: ignore[method-assign]
        await host.start()
        client = AldwinClient(transport.connect())
        await client.connect()
        try:
            status = await client.connectors_add(
                url=f"{url}/mcp", name="fixture", auth="bearer", token=TOKEN
            )
            assert isinstance(status, ConnectorStatusEvent)
            assert status.status == "connected"
            fake_model.add_turns(
                tool_call("mcp__fixture__read_thing", {}), final("done")
            )
            async for _ev in client.turn("use the tool"):
                pass
            assert controls.requests[-1]["authorization"] == f"Bearer {TOKEN}"
        finally:
            await client.close()
            await host.stop_host()

    creds = credentials_dir(aldwin_home)
    assert _files_containing(aldwin_home, TOKEN) == [creds / "fixture.json"]
    assert _mode(creds / "fixture.json") == 0o600
    assert _mode(creds) == 0o700
    assert _mode(creds.parent) == 0o700
    assert sent, "expected protocol events"
    for event in sent:
        assert TOKEN not in event.model_dump_json()


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read_file", {"path": "{cred}"}),
        ("glob", {"pattern": "*.json", "path": "{dir}"}),
        ("grep", {"pattern": "token", "path": "{dir}"}),
        ("list_dir", {"path": "{dir}"}),
    ],
)
def test_tools_cannot_reach_connector_credentials(
    workspace: Workspace, aldwin_home: Path, tool: str, args: dict[str, str]
) -> None:
    creds = credentials_dir(aldwin_home)
    creds.mkdir(parents=True)
    cred = creds / "github.json"
    cred.write_text('{"token": "x"}', encoding="utf-8")
    filled = {k: v.format(cred=cred, dir=creds) for k, v in args.items()}
    session = PolicySession(workspace=workspace, home=aldwin_home)
    assert decide(tool, filled, session).kind == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.aldwin/credentials/mcp/github.json",
        "cat /Users/someone/.aldwin/credentials/mcp/github.json",
        "grep token ~/.aldwin/credentials/mcp/*.json",
    ],
)
def test_run_command_cannot_read_connector_credentials(
    workspace: Workspace, aldwin_home: Path, command: str
) -> None:
    """The command floor blocks any path through `.aldwin/` (the default home)."""
    session = PolicySession(workspace=workspace, home=aldwin_home)
    decision = decide("run_command", {"command": command}, session)
    assert decision.kind == "deny"
