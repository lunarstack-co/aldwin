"""Pre-flight discovery, client identity and the PKCE check."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx2
import pytest
from fixtures.oauth_server import OAuthFixture, OAuthFixtureConfig, serve_oauth
from mcp.shared.auth import OAuthMetadata

from aldwin.core.connectors import oauth
from aldwin.core.connectors.config import ConnectorConfig, OAuthSettings
from aldwin.core.connectors.errors import ConnectorUnreachable, SignInError
from aldwin.core.connectors.oauth import (
    choose_client_identity,
    client_metadata,
    discover,
    require_s256,
)

pytestmark = pytest.mark.loopback
CIMD = "https://askaldwin.com/oauth/client.json"


def _config(url: str, **kw: Any) -> ConnectorConfig:
    return ConnectorConfig(name="fixture", url=url, auth="oauth", **kw)


@pytest.fixture
def serve() -> Callable[..., AbstractAsyncContextManager[OAuthFixture]]:
    def start(**settings: Any) -> AbstractAsyncContextManager[OAuthFixture]:
        return serve_oauth(OAuthFixture(config=OAuthFixtureConfig(**settings)))

    return start


@pytest.mark.parametrize("prm_in_header", [True, False])
async def test_discovers_metadata(serve: Any, prm_in_header: bool) -> None:
    async with serve(prm_in_header=prm_in_header) as fx, httpx2.AsyncClient() as http:
        found = await discover(_config(fx.mcp_url), client=http)
    assert found is not None
    assert found.issuer == fx.issuer
    assert found.prm is not None
    assert str(found.asm.token_endpoint) == f"{fx.base_url}/token"


async def test_no_sign_in_needed_returns_none(fixture_url: str) -> None:
    async with httpx2.AsyncClient() as http:
        assert await discover(_config(f"{fixture_url}/mcp"), client=http) is None


def _asm(**kw: Any) -> OAuthMetadata:
    return OAuthMetadata(
        issuer="https://auth.example",
        authorization_endpoint="https://auth.example/authorize",
        token_endpoint="https://auth.example/token",
        **kw,
    )


def test_identity_order() -> None:
    both = _asm(
        client_id_metadata_document_supported=True,
        registration_endpoint="https://auth.example/register",
    )
    pre = _config("https://x.example/mcp", oauth=OAuthSettings(client_id="mine"))
    assert choose_client_identity(pre, both, CIMD).kind == "preregistered"
    assert choose_client_identity(pre, both, CIMD).client_id == "mine"
    plain = _config("https://x.example/mcp")
    assert choose_client_identity(plain, both, CIMD).kind == "cimd"
    dcr_only = _asm(registration_endpoint="https://auth.example/register")
    assert choose_client_identity(plain, dcr_only, CIMD).kind == "dcr"
    with pytest.raises(SignInError) as info:
        choose_client_identity(plain, _asm(), CIMD)
    assert info.value.message == (
        "Fixture needs an app registration Aldwin doesn't have. Add a client ID "
        "with: aldwin connectors add fixture --url … --client-id …"
    )


def test_s256_is_required() -> None:
    require_s256("Fixture", _asm(code_challenge_methods_supported=["S256"]))
    for methods in (None, ["plain"]):
        with pytest.raises(SignInError) as info:
            require_s256("Fixture", _asm(code_challenge_methods_supported=methods))
        assert info.value.message == (
            "Fixture's sign-in server doesn't support a secure sign-in (PKCE)."
        )


def test_client_metadata_puts_the_bound_port_first() -> None:
    meta = client_metadata(47824)
    assert [str(u) for u in meta.redirect_uris or []] == [
        "http://127.0.0.1:47824/callback",
        "http://127.0.0.1:47823/callback",
        "http://127.0.0.1:47825/callback",
    ]
    assert meta.token_endpoint_auth_method == "none"
    assert meta.application_type == "native"


async def test_discovery_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.Event().wait()

    server = await asyncio.start_server(never, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(oauth, "DISCOVERY_TIMEOUT_S", 0.2)
    try:
        async with httpx2.AsyncClient() as http:
            with pytest.raises(ConnectorUnreachable) as info:
                await discover(_config(f"http://127.0.0.1:{port}/mcp"), client=http)
        assert info.value.message == f"Couldn't reach 127.0.0.1:{port}: timed out."
    finally:
        server.close()
