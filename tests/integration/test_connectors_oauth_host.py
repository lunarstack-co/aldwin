"""Browser sign-in end to end: host, client, fake model and the OAuth fixture."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml
from conftest import collect
from connectors.oauth_helpers import seed_signed_in
from fakes.model import FakeModel, final, tool_call
from fixtures.oauth_server import OAuthFixture

from aldwin.client import AldwinClient, ProtocolError
from aldwin.core.agent import build_agent
from aldwin.core.connectors.config import connectors_path
from aldwin.core.connectors.credentials import FileCredentialStore
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import (
    ConnectorAuthEvent,
    ConnectorStatusEvent,
    DoneEvent,
    Event,
    NoticeEvent,
    ToolResultEvent,
)

pytestmark = pytest.mark.loopback

User = Callable[[AldwinClient, ConnectorAuthEvent], Awaitable[None]]


@dataclass
class Rig:
    host: AldwinHost
    client: AldwinClient
    model: FakeModel
    store: FileCredentialStore


@pytest.fixture
def start_rig(fake_model: FakeModel, aldwin_home: Path) -> Callable[[], Awaitable[Rig]]:
    rigs: list[Rig] = []

    async def start() -> Rig:
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
        await host.start()
        client = AldwinClient(transport.connect())
        await client.connect()
        rig = Rig(host, client, fake_model, FileCredentialStore(aldwin_home))
        rigs.append(rig)
        return rig

    return start


@pytest.fixture
async def rig(start_rig: Callable[[], Awaitable[Rig]]) -> AsyncIterator[Rig]:
    rig = await start_rig()
    try:
        yield rig
    finally:
        await rig.client.close()
        await rig.host.stop_host()


async def _approve_in_browser(url: str) -> str:
    """Approve on the sign-in page; return where the browser is sent back."""
    async with httpx2.AsyncClient(follow_redirects=False) as http:
        approved = await http.get(url)
    return str(approved.headers["location"])


async def browser(_client: AldwinClient, event: ConnectorAuthEvent) -> None:
    assert event.url is not None
    back = await _approve_in_browser(event.url)
    async with httpx2.AsyncClient() as http:
        await http.get(back)


async def paste(client: AldwinClient, event: ConnectorAuthEvent) -> None:
    assert event.url is not None
    back = await _approve_in_browser(event.url)
    await client.connectors_auth_paste(event.name, back)


async def _sign_in(rig: Rig, user: User, **kwargs: Any) -> list[Event]:
    events: list[Event] = []
    async for event in rig.client.connectors_sign_in(name="fixture", **kwargs):
        events.append(event)
        if isinstance(event, ConnectorAuthEvent):
            await user(rig.client, event)
    return events


def _saved(home: Path) -> dict[str, Any]:
    data = yaml.safe_load(connectors_path(home).read_text("utf-8"))
    return dict(data["connectors"]["fixture"])


async def test_add_signs_in_through_the_browser(
    rig: Rig, oauth_server: OAuthFixture, aldwin_home: Path
) -> None:
    """`TC-01`."""
    events = await _sign_in(rig, browser, url=oauth_server.mcp_url)

    [prompt] = [e for e in events if isinstance(e, ConnectorAuthEvent)]
    assert (prompt.mode, prompt.title, prompt.expires_in) == ("browser", "Fixture", 300)
    assert prompt.url is not None and prompt.url.startswith(oauth_server.issuer)
    status = [e for e in events if isinstance(e, ConnectorStatusEvent)][-1]
    assert (status.status, status.tools) == ("connected", 5)
    assert isinstance(events[-1], DoneEvent) and events[-1].reason == "ok"
    assert _saved(aldwin_home)["auth"] == "oauth"
    record = rig.store.get("fixture")
    assert record is not None and record["kind"] == "oauth"


async def test_paste_mode_signs_in(rig: Rig, oauth_server: OAuthFixture) -> None:
    """`TC-06`."""
    events = await _sign_in(rig, paste, url=oauth_server.mcp_url, no_browser=True)
    [prompt] = [e for e in events if isinstance(e, ConnectorAuthEvent)]
    assert prompt.mode == "paste"
    assert isinstance(events[-1], DoneEvent)
    assert rig.store.get("fixture") is not None


async def test_bad_paste_is_refused_and_the_wait_goes_on(
    rig: Rig, oauth_server: OAuthFixture
) -> None:
    async def wrong_then_right(client: AldwinClient, event: ConnectorAuthEvent) -> None:
        with pytest.raises(ProtocolError) as info:
            await client.connectors_auth_paste(event.name, "http://127.0.0.1/x")
        assert info.value.code == "connector"
        await paste(client, event)

    events = await _sign_in(
        rig, wrong_then_right, url=oauth_server.mcp_url, no_browser=True
    )
    assert isinstance(events[-1], DoneEvent)


async def test_stop_cancels_a_waiting_sign_in(
    rig: Rig, oauth_server: OAuthFixture, aldwin_home: Path
) -> None:
    """Esc on the sign-in screen."""

    async def press_esc(client: AldwinClient, _event: ConnectorAuthEvent) -> None:
        stopped = await client.stop()
        assert isinstance(stopped, DoneEvent)

    events = await _sign_in(rig, press_esc, url=oauth_server.mcp_url)
    assert isinstance(events[-1], DoneEvent) and events[-1].reason == "stopped"
    assert rig.store.get("fixture") is None
    assert not connectors_path(aldwin_home).exists()
    listing = await rig.client.connectors_list()
    assert [i.name for i in listing.items] == []


async def test_sign_out_keeps_tools_that_say_to_sign_in(
    rig: Rig, oauth_server: OAuthFixture
) -> None:
    await _sign_in(rig, browser, url=oauth_server.mcp_url)

    status = await rig.client.connectors_logout("fixture")
    assert status.status == "needs_auth"
    assert rig.store.get("fixture") is None

    rig.model.add_turns(tool_call("mcp__fixture__read_thing", {}), final("ok"))
    events = await collect(rig.client.turn("read it"))
    [result] = [e for e in events if isinstance(e, ToolResultEvent)]
    assert result.ok is False
    seen = " ".join(str(m.content) for m in rig.model.received_messages)
    assert (
        "Fixture needs you to sign in again. Ask the user to run /connectors." in seen
    )


async def test_sign_in_again_after_sign_out(
    rig: Rig, oauth_server: OAuthFixture
) -> None:
    await _sign_in(rig, browser, url=oauth_server.mcp_url)
    await rig.client.connectors_logout("fixture")

    events = await _sign_in(rig, browser)
    status = [e for e in events if isinstance(e, ConnectorStatusEvent)][-1]
    assert status.status == "connected"


async def test_oauth_connector_without_sign_in_waits_at_start(
    start_rig: Callable[[], Awaitable[Rig]],
    oauth_server: OAuthFixture,
    aldwin_home: Path,
) -> None:
    """`TC-35`: no discovery, no browser; one notice on the next turn."""
    connectors_path(aldwin_home).write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "connectors": {
                    "fixture": {"url": oauth_server.mcp_url, "auth": "oauth"}
                },
            }
        ),
        encoding="utf-8",
    )
    rig = await start_rig()
    try:
        listing = await rig.client.connectors_list()
        assert [i.status for i in listing.items] == ["needs_auth"]
        assert oauth_server.requests["well_known"] == 0

        rig.model.add_turns(final("hi"), final("again"))
        first = await collect(rig.client.turn("hello"))
        notices = [e.message for e in first if isinstance(e, NoticeEvent)]
        assert notices == ["Fixture needs you to sign in again: /connectors."]
        second = await collect(rig.client.turn("hello"))
        assert not any(isinstance(e, NoticeEvent) for e in second)
    finally:
        await rig.client.close()
        await rig.host.stop_host()


async def test_revoked_sign_in_moves_to_needs_auth(
    start_rig: Callable[[], Awaitable[Rig]],
    oauth_server: OAuthFixture,
    aldwin_home: Path,
) -> None:
    store = FileCredentialStore(aldwin_home)
    seed_signed_in(oauth_server, store)
    oauth_server.revoke_all()
    connectors_path(aldwin_home).write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "connectors": {
                    "fixture": {"url": oauth_server.mcp_url, "auth": "oauth"}
                },
            }
        ),
        encoding="utf-8",
    )
    rig = await start_rig()
    try:
        async for event in rig.client.host_events():
            if isinstance(event, ConnectorStatusEvent) and event.status == "needs_auth":
                break
        assert oauth_server.requests["register"] == 0
        assert oauth_server.requests["authorize"] == 0
    finally:
        await rig.client.close()
        await rig.host.stop_host()


async def test_token_mode_asks_for_a_token(
    rig: Rig, oauth_server: OAuthFixture
) -> None:
    """`TC-52`: a bearer server with no token asks through `connector.auth`."""
    client_id = oauth_server.register({"redirect_uris": ["http://127.0.0.1/cb"]})[1][
        "client_id"
    ]
    token = oauth_server._issue(client_id, refresh=False)["access_token"]

    async def type_token(client: AldwinClient, event: ConnectorAuthEvent) -> None:
        assert (event.mode, event.url) == ("token", None)
        await client.connectors_auth_token(event.name, token)

    events = await _sign_in(rig, type_token, url=oauth_server.mcp_url, auth="bearer")
    assert isinstance(events[-1], DoneEvent)
    record = rig.store.get("fixture")
    assert record is not None and record["token"] == token


async def test_dry_run_of_an_oauth_connector_is_refused(
    rig: Rig, oauth_server: OAuthFixture
) -> None:
    with pytest.raises(ProtocolError) as info:
        await rig.client.connectors_add(
            url=oauth_server.mcp_url, name="fixture", auth="oauth", dry_run=True
        )
    assert info.value.code == "connector"


async def test_server_without_sign_in_connects_without_it(
    rig: Rig, fixture_url: str, aldwin_home: Path
) -> None:
    events = await _sign_in(rig, browser, url=f"{fixture_url}/mcp")
    notices = [e.message for e in events if isinstance(e, NoticeEvent)]
    assert notices == ["Fixture doesn't need sign-in, so it connected without it."]
    assert isinstance(events[-1], DoneEvent)
    assert _saved(aldwin_home)["auth"] == "none"


async def test_list_carries_the_catalogue(rig: Rig, oauth_server: OAuthFixture) -> None:
    listing = await rig.client.connectors_list()
    assert [c.name for c in listing.catalog][:2] == ["notion", "linear"]
    assert len(listing.catalog) == 9
    assert not any(c.added for c in listing.catalog)


async def test_manager_refuses_a_sign_in_nobody_can_answer(
    rig: Rig, oauth_server: OAuthFixture
) -> None:
    from aldwin.core.connectors.config import ConnectorConfig
    from aldwin.core.connectors.errors import NeedsSignIn

    config = ConnectorConfig(name="fixture", url=oauth_server.mcp_url, auth="oauth")
    with pytest.raises(NeedsSignIn) as info:
        await rig.host._connectors.add(config, token=None, dry_run=False)
    assert info.value.message == "Fixture needs you to sign in: /connectors."
    assert oauth_server.requests["well_known"] == 0
