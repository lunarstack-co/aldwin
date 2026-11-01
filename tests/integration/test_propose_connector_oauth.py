"""Asking Aldwin to connect an app that signs in through the browser, end to
end against the OAuth fixture server on 127.0.0.1."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fakes.model import FakeModel, final, tool_call
from fixtures.oauth_server import OAuthFixture

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.connectors.catalog import CatalogEntry
from aldwin.core.connectors.config import load_connectors
from aldwin.core.connectors.credentials import FileCredentialStore
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import ApprovalEvent, ConnectorAuthEvent, Event

pytestmark = pytest.mark.loopback


@pytest.fixture
async def host_and_client(
    fake_model: FakeModel, aldwin_home: Path, oauth_server: OAuthFixture
) -> AsyncIterator[tuple[AldwinHost, AldwinClient]]:
    holder: list[AldwinHost] = []

    def builder(model_name: str, ask: Any) -> Any:
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
            connectors_block=lambda: (
                holder[0]._connectors.context_block() if holder else ""
            ),
        )

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder)
    holder.append(host)
    host._connectors._catalog = (
        CatalogEntry(
            name="fixture",
            title="Fixture",
            purpose="Signs in through the browser",
            url=oauth_server.mcp_url,
            auth="oauth",
            docs_url="https://fixture.example",
        ),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        yield host, client
    finally:
        await client.close()
        await host.stop_host()


async def _finish_in_browser(event: ConnectorAuthEvent) -> None:
    """Approve on the sign-in page, then follow the redirect back to Aldwin."""
    assert event.url is not None
    async with httpx2.AsyncClient(follow_redirects=False) as http:
        approved = await http.get(event.url)
    async with httpx2.AsyncClient() as http:
        await http.get(str(approved.headers["location"]))


async def test_browser_sign_in_from_a_proposal(
    host_and_client: tuple[AldwinHost, AldwinClient],
    fake_model: FakeModel,
    aldwin_home: Path,
    oauth_server: OAuthFixture,
) -> None:
    host, client = host_and_client
    fake_model.add_turns(
        tool_call("propose_connector", {"name": "fixture"}), final("done")
    )
    events: list[Event] = []
    async for ev in client.turn("connect the fixture"):
        events.append(ev)
        if isinstance(ev, ApprovalEvent):
            assert ev.detail == f"{oauth_server.mcp_url} · sign in with your browser"
            await client.approve(ev.approval_id, "yes", digest=ev.digest)
        elif isinstance(ev, ConnectorAuthEvent):
            assert ev.mode == "browser" and ev.re == events[0].re
            await _finish_in_browser(ev)

    tool_messages = [m for m in fake_model.calls[-1] if m.role == "tool"]
    result = str(tool_messages[-1].content)
    assert "connected: fixture, " in result and "tools loaded" in result
    assert oauth_server.issuer not in result
    assert "fixture" in load_connectors(aldwin_home).entries
    assert FileCredentialStore(aldwin_home).get("fixture") is not None
    assert any(n.startswith("mcp__fixture__") for n in host._agent_session.tools)
