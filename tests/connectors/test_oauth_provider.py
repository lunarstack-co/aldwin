"""`AldwinOAuthProvider`: silent refresh, the fence and the background rule."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx2
import pytest

from aldwin.core.connectors.connection import build_auth
from aldwin.core.connectors.credentials import FileCredentialStore
from aldwin.core.connectors.errors import NeedsSignIn
from aldwin.core.connectors.oauth import AldwinOAuthProvider, background_provider
from connectors.oauth_helpers import oauth_config, seed_signed_in

pytestmark = pytest.mark.loopback

MCP_BODY = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
HEADERS = {"accept": "application/json, text/event-stream"}


async def _call(provider: AldwinOAuthProvider, fixture: Any) -> httpx2.Response:
    async with httpx2.AsyncClient(auth=provider) as http:
        return await http.post(fixture.mcp_url, json=MCP_BODY, headers=HEADERS)


def _no_discovery(fixture: Any) -> None:
    assert fixture.requests["well_known"] == 0
    assert fixture.requests["register"] == 0  # the seed registers in-process
    assert fixture.requests["authorize"] == 0


async def test_valid_token_is_sent(oauth_server: Any, aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    seed_signed_in(oauth_server, store)
    response = await _call(
        background_provider(oauth_config(oauth_server), store), oauth_server
    )
    assert response.status_code != 401
    assert oauth_server.requests["token:refresh_token"] == 0


async def test_expired_token_refreshes_once_silently(
    oauth_server: Any, aldwin_home: Path
) -> None:
    """`TC-07`: the server expired the token; one refresh, then the call works."""
    store = FileCredentialStore(aldwin_home)
    seed_signed_in(oauth_server, store)
    oauth_server.expire_access_tokens()
    response = await _call(
        background_provider(oauth_config(oauth_server), store), oauth_server
    )
    assert response.status_code != 401
    assert oauth_server.requests["token:refresh_token"] == 1
    _no_discovery(oauth_server)
    stored = store.get("fixture")
    assert (
        stored is not None
        and stored["tokens"]["access_token"] in oauth_server.access_tokens
    )


async def test_near_expiry_refreshes_before_the_request(
    oauth_server: Any, aldwin_home: Path
) -> None:
    """After a restart, a token inside the 60 s window is refreshed first."""
    store = FileCredentialStore(aldwin_home)
    seed_signed_in(oauth_server, store, expires_in=10)
    response = await _call(
        background_provider(oauth_config(oauth_server), store), oauth_server
    )
    assert response.status_code != 401
    assert oauth_server.requests["token:refresh_token"] == 1
    assert oauth_server.requests["mcp"] == 1  # no 401 round trip first


async def test_revoked_refresh_needs_sign_in_without_discovery(
    oauth_server: Any, aldwin_home: Path
) -> None:
    store = FileCredentialStore(aldwin_home)
    seed_signed_in(oauth_server, store)
    oauth_server.revoke_all()
    provider = background_provider(oauth_config(oauth_server), store)
    with pytest.raises(NeedsSignIn) as info:
        await _call(provider, oauth_server)
    assert info.value.message == "Fixture needs you to sign in again: /connectors."
    assert oauth_server.requests["token:refresh_token"] == 1
    _no_discovery(oauth_server)

    # The flow released its lock: the next request fails fast instead of hanging.
    with pytest.raises(NeedsSignIn):
        await asyncio.wait_for(_call(provider, oauth_server), 5)


async def test_two_refreshers_send_one_refresh(
    oauth_server: Any, aldwin_home: Path
) -> None:
    """`TC-08`: the fence lets one refresh happen; the other adopts its tokens."""
    store = FileCredentialStore(aldwin_home)
    seed_signed_in(oauth_server, store, expires_in=-5)
    config = oauth_config(oauth_server)
    first, second = (
        background_provider(config, store),
        background_provider(config, store),
    )
    responses = await asyncio.gather(
        _call(first, oauth_server), _call(second, oauth_server)
    )
    assert all(r.status_code != 401 for r in responses)
    assert oauth_server.requests["token:refresh_token"] == 1


def test_build_auth_gives_a_background_provider(aldwin_home: Path) -> None:
    from aldwin.core.connectors.config import ConnectorConfig

    config = ConnectorConfig(name="x", url="https://x.example/mcp", auth="oauth")
    auth = build_auth(config, FileCredentialStore(aldwin_home), {})
    assert isinstance(auth, AldwinOAuthProvider)
    assert auth.interactive is False
