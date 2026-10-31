"""Interactive browser sign-in against the OAuth fixture server."""

from __future__ import annotations

import os
import stat
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fixtures.oauth_server import OAuthFixture, OAuthFixtureConfig, serve_oauth

from aldwin.core.connectors import sign_in as sign_in_module
from aldwin.core.connectors.config import (
    ConnectorConfig,
    OAuthSettings,
    credentials_dir,
)
from aldwin.core.connectors.connection import McpConnection
from aldwin.core.connectors.credentials import FileCredentialStore
from aldwin.core.connectors.errors import ConnectorError, SignInError, SignInTimedOut
from aldwin.core.connectors.oauth import AldwinOAuthProvider
from aldwin.core.connectors.oauth_messages import (
    DEFAULT_CLIENT_METADATA_URL,
    SIGN_IN_TIMEOUT_S,
)
from aldwin.core.connectors.sign_in import SignInWait, sign_in
from connectors.oauth_helpers import oauth_config, seed_signed_in

pytestmark = pytest.mark.loopback


async def _ignore(*_args: Any) -> None:
    return None


def _connect(config: ConnectorConfig) -> Callable[[AldwinOAuthProvider], McpConnection]:
    def connect(provider: AldwinOAuthProvider) -> McpConnection:
        return McpConnection(
            config, auth=provider, on_tools_changed=_ignore, on_lost=_ignore
        )

    return connect


def _browser(
    wait: SignInWait, *, paste: bool = False, seen: list[str] | None = None
) -> Callable[[str], Awaitable[None]]:
    """Play the user: open the address, approve, and come back or paste."""

    async def on_auth_url(url: str) -> None:
        if seen is not None:
            seen.append(url)
        async with httpx2.AsyncClient(follow_redirects=False) as http:
            approved = await http.get(url)
            back = approved.headers["location"]
            if paste:
                wait.paste(back)
            else:
                await http.get(back)

    return on_auth_url


async def _run(
    fixture: Any,
    store: FileCredentialStore,
    config: ConnectorConfig | None = None,
    *,
    paste: bool = False,
    seen: list[str] | None = None,
) -> Any:
    config = config or oauth_config(fixture)
    wait = SignInWait(config.name)
    return await sign_in(
        config,
        store,
        cimd_url=DEFAULT_CLIENT_METADATA_URL,
        no_browser=paste,
        wait=wait,
        on_auth_url=_browser(wait, paste=paste, seen=seen),
        connect=_connect(config),
    )


@pytest.fixture
def serve() -> Callable[..., AbstractAsyncContextManager[OAuthFixture]]:
    def start(**settings: Any) -> AbstractAsyncContextManager[OAuthFixture]:
        return serve_oauth(OAuthFixture(config=OAuthFixtureConfig(**settings)))

    return start


async def test_dcr_sign_in_returns_tools_and_stores_credentials(
    oauth_server: Any, aldwin_home: Path
) -> None:
    """`TC-01` (core half)."""
    store = FileCredentialStore(aldwin_home)
    signed = await _run(oauth_server, store)
    try:
        assert {s.name for s in signed.specs} >= {"read_thing", "write_thing"}
        data = store.get("fixture")
        assert data is not None
        assert (data["kind"], data["issuer"]) == ("oauth", oauth_server.issuer)
        assert data["tokens"]["access_token"] in oauth_server.access_tokens
        path = credentials_dir(aldwin_home) / "fixture.json"
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert oauth_server.requests["register"] == 1
        params = oauth_server.last_authorize_params
        assert params["code_challenge_method"] == "S256"
        assert params["resource"] == oauth_server.mcp_url
    finally:
        await signed.connection.close()


async def test_cimd_used_when_advertised(serve: Any, aldwin_home: Path) -> None:
    """`TC-02`."""
    async with serve(cimd=True) as fx:
        signed = await _run(fx, FileCredentialStore(aldwin_home))
        await signed.connection.close()
    assert fx.last_authorize_params["client_id"] == DEFAULT_CLIENT_METADATA_URL
    assert fx.requests["register"] == 0


async def test_preregistered_client_skips_registration(
    oauth_server: Any, aldwin_home: Path
) -> None:
    oauth_server.clients["pre-id"] = [
        f"http://127.0.0.1:{p}/callback" for p in (47823, 47824, 47825)
    ]
    config = ConnectorConfig(
        name="fixture",
        url=oauth_server.mcp_url,
        auth="oauth",
        oauth=OAuthSettings(client_id="pre-id"),
    )
    signed = await _run(oauth_server, FileCredentialStore(aldwin_home), config)
    await signed.connection.close()
    assert oauth_server.last_authorize_params["client_id"] == "pre-id"
    assert oauth_server.requests["register"] == 0


async def test_no_s256_refused_before_browser(serve: Any, aldwin_home: Path) -> None:
    """`TC-03`."""
    seen: list[str] = []
    async with serve(s256=False) as fx:
        with pytest.raises(SignInError) as info:
            await _run(fx, FileCredentialStore(aldwin_home), seen=seen)
    assert info.value.message == (
        "Fixture's sign-in server doesn't support a secure sign-in (PKCE)."
    )
    assert seen == []
    assert fx.requests["register"] == 0


async def test_paste_completes_sign_in(oauth_server: Any, aldwin_home: Path) -> None:
    """`TC-06` (core half)."""
    store = FileCredentialStore(aldwin_home)
    signed = await _run(oauth_server, store, paste=True)
    await signed.connection.close()
    assert store.get("fixture") is not None


async def test_timeout_leaves_no_file(
    oauth_server: Any, aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sign_in_module, "SIGN_IN_TIMEOUT_S", 0.2)
    store = FileCredentialStore(aldwin_home)
    config = oauth_config(oauth_server)
    with pytest.raises(SignInTimedOut):
        await sign_in(
            config,
            store,
            cimd_url=DEFAULT_CLIENT_METADATA_URL,
            no_browser=False,
            wait=SignInWait("fixture"),
            on_auth_url=_ignore,
            connect=_connect(config),
        )
    assert store.get("fixture") is None


async def test_same_issuer_reuses_the_client(
    oauth_server: Any, aldwin_home: Path
) -> None:
    store = FileCredentialStore(aldwin_home)
    seed_signed_in(oauth_server, store)
    oauth_server.revoke_all()
    signed = await _run(oauth_server, store)
    await signed.connection.close()
    assert oauth_server.requests["register"] == 0


async def test_new_issuer_registers_again(oauth_server: Any, aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    record = seed_signed_in(oauth_server, store)
    store.set("fixture", {**record, "issuer": "https://old.example"})
    signed = await _run(oauth_server, store)
    await signed.connection.close()
    assert oauth_server.requests["register"] == 1
    data = store.get("fixture")
    assert data is not None and data["issuer"] == oauth_server.issuer


async def test_registration_refused(serve: Any, aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    async with serve(register_status=403) as fx:
        with pytest.raises(ConnectorError) as info:
            await _run(fx, store)
    assert info.value.message.startswith("Fixture refused Aldwin's app registration.")
    assert store.get("fixture") is None


async def test_server_without_sign_in_returns_none(
    fixture_url: str, aldwin_home: Path
) -> None:
    config = ConnectorConfig(name="fixture", url=f"{fixture_url}/mcp", auth="oauth")
    result = await sign_in(
        config,
        FileCredentialStore(aldwin_home),
        cimd_url=DEFAULT_CLIENT_METADATA_URL,
        no_browser=False,
        wait=SignInWait("fixture"),
        on_auth_url=_ignore,
        connect=_connect(config),
    )
    assert result is None


def test_bad_pastes_are_refused_without_ending_the_wait() -> None:
    wait = SignInWait("fixture")
    with pytest.raises(SignInError, match="No sign-in is waiting for fixture."):
        wait.paste("http://127.0.0.1:47823/callback?code=c&state=s")
    wait.expected_state = "s"
    with pytest.raises(SignInError, match="different sign-in"):
        wait.paste("http://127.0.0.1:47823/callback?code=c&state=other")
    wait.paste("http://127.0.0.1:47823/callback?code=c&state=s")
    assert wait.replies.qsize() == 1


async def test_open_waits_longer_than_the_sign_in(
    oauth_server: Any, aldwin_home: Path
) -> None:
    timeouts: list[float] = []
    config = oauth_config(oauth_server)

    class _Recording(McpConnection):
        async def open(self, *, timeout: float = 30.0) -> Any:
            timeouts.append(timeout)
            return await super().open(timeout=timeout)

    wait = SignInWait("fixture")
    signed = await sign_in(
        config,
        FileCredentialStore(aldwin_home),
        cimd_url=DEFAULT_CLIENT_METADATA_URL,
        no_browser=False,
        wait=wait,
        on_auth_url=_browser(wait),
        connect=lambda provider: _Recording(
            config, auth=provider, on_tools_changed=_ignore, on_lost=_ignore
        ),
    )
    await signed.connection.close()
    assert timeouts and timeouts[0] > SIGN_IN_TIMEOUT_S


async def test_declined_in_the_browser(oauth_server: Any, aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    config = oauth_config(oauth_server)

    async def decline(url: str) -> None:
        state = parse_qs(urlsplit(url).query)["state"][0]
        async with httpx2.AsyncClient() as http:
            await http.get(
                "http://127.0.0.1:47823/callback",
                params={"error": "access_denied", "state": state},
            )

    with pytest.raises(SignInError) as info:
        await sign_in(
            config,
            store,
            cimd_url=DEFAULT_CLIENT_METADATA_URL,
            no_browser=False,
            wait=SignInWait("fixture"),
            on_auth_url=decline,
            connect=_connect(config),
        )
    assert info.value.message == (
        "Fixture sign-in was declined in the browser. Try again from /connectors."
    )
    assert store.get("fixture") is None
