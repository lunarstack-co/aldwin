"""Browser sign-in for connectors: pre-flight discovery, client identity, and
the SDK OAuth provider with Aldwin's refresh and background rules.

Imports `mcp`, so only sign-in and OAuth connections load it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    extract_resource_metadata_from_www_auth,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    issuers_match,
    validate_metadata_issuer,
)
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientMetadata,
    OAuthMetadata,
    ProtectedResourceMetadata,
)

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.credentials import CredentialStore
from aldwin.core.connectors.errors import ConnectorUnreachable, NeedsSignIn, SignInError
from aldwin.core.connectors.oauth_messages import (
    CALLBACK_PATH,
    CALLBACK_PORTS,
    DISCOVERY_TIMEOUT_S,
    REFRESH_SKEW_S,
    issuer_mismatch,
    needs_registration,
    needs_sign_in_notice,
    no_browser_sign_in,
    pkce_unsupported,
)
from aldwin.core.connectors.token_storage import CredentialTokenStorage, refresh_fence

# An MCP `initialize` body: enough for a server to answer 401 or 200.
_PROBE_BODY = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "aldwin", "version": "0"},
    },
}


@dataclass(frozen=True)
class Discovery:
    """Where and how a server signs users in."""

    prm: ProtectedResourceMetadata | None
    asm: OAuthMetadata
    issuer: str


@dataclass(frozen=True)
class ClientIdentity:
    kind: Literal["preregistered", "cimd", "dcr"]
    client_id: str | None = None


def _host(url: str) -> str:
    return urlsplit(url).netloc or url


async def discover(
    config: ConnectorConfig, *, client: httpx2.AsyncClient
) -> Discovery | None:
    """Find the server's sign-in metadata; `None` when it needs no sign-in."""
    title = config.display_title
    try:
        async with asyncio.timeout(DISCOVERY_TIMEOUT_S):
            return await _discover(config, client)
    except TimeoutError:
        raise ConnectorUnreachable(_host(config.url), "timed out") from None
    except httpx2.HTTPError as exc:
        raise ConnectorUnreachable(
            _host(config.url), str(exc) or type(exc).__name__
        ) from None
    except OAuthFlowError:
        raise SignInError(issuer_mismatch(title)) from None


async def _discover(
    config: ConnectorConfig, client: httpx2.AsyncClient
) -> Discovery | None:
    probe = await client.post(
        config.url,
        json=_PROBE_BODY,
        headers={"accept": "application/json, text/event-stream"},
    )
    if probe.is_success:
        return None
    if probe.status_code != 401:
        raise ConnectorUnreachable(_host(config.url), f"HTTP {probe.status_code}")

    prm: ProtectedResourceMetadata | None = None
    hinted = extract_resource_metadata_from_www_auth(probe)
    for url in build_protected_resource_metadata_discovery_urls(hinted, config.url):
        prm = await handle_protected_resource_response(
            await client.send(create_oauth_metadata_request(url))
        )
        if prm is not None:
            break

    auth_server = str(prm.authorization_servers[0]) if prm else None
    expected = auth_server or f"{urlsplit(config.url).scheme}://{_host(config.url)}"
    for url in build_oauth_authorization_server_metadata_discovery_urls(
        auth_server, config.url
    ):
        ok, asm = await handle_auth_metadata_response(
            await client.send(create_oauth_metadata_request(url))
        )
        if not ok:
            break
        if asm is not None:
            if auth_server is None and issuers_match(str(asm.issuer), expected):
                expected = str(asm.issuer)
            validate_metadata_issuer(asm, expected)
            return Discovery(prm=prm, asm=asm, issuer=str(asm.issuer))
    raise SignInError(no_browser_sign_in(config.display_title, config.name))


def choose_client_identity(
    config: ConnectorConfig, asm: OAuthMetadata, cimd_url: str
) -> ClientIdentity:
    """Pre-registered client ID, then CIMD, then dynamic registration, else stop."""
    if config.oauth is not None and config.oauth.client_id:
        return ClientIdentity("preregistered", config.oauth.client_id)
    if asm.client_id_metadata_document_supported is True:
        return ClientIdentity("cimd", cimd_url)
    if asm.registration_endpoint is not None:
        return ClientIdentity("dcr")
    raise SignInError(needs_registration(config.display_title, config.name))


def require_s256(title: str, asm: OAuthMetadata) -> None:
    """Refuse a server without PKCE S256; an absent list means no PKCE at all."""
    if "S256" not in (asm.code_challenge_methods_supported or []):
        raise SignInError(pkce_unsupported(title))


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}{CALLBACK_PATH}"


def client_metadata(redirect_port: int) -> OAuthClientMetadata:
    """What Aldwin registers. The bound port comes first: the SDK sends `[0]`."""
    ports = [redirect_port, *(p for p in CALLBACK_PORTS if p != redirect_port)]
    return OAuthClientMetadata(
        client_name="Aldwin",
        client_uri="https://askaldwin.com",
        redirect_uris=[redirect_uri(p) for p in ports],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


class AldwinOAuthProvider(OAuthClientProvider):
    """The SDK OAuth provider with Aldwin's rules on top.

    * A token near expiry is refreshed first, under a cross-process fence.
    * A 401 gets one refresh before anything else.
    * Without a person (`interactive=False`) a 401 that refresh did not fix
      raises `NeedsSignIn` before the SDK can discover, register a client or
      open a browser.
    """

    def __init__(
        self,
        *,
        config: ConnectorConfig,
        storage: CredentialTokenStorage,
        fence_dir: Path,
        interactive: bool,
        redirect_port: int = CALLBACK_PORTS[0],
        redirect_handler: Callable[[str], Awaitable[None]] | None = None,
        callback_handler: Callable[[], Awaitable[AuthorizationCodeResult]]
        | None = None,
        client_metadata_url: str | None = None,
    ) -> None:
        super().__init__(
            server_url=config.url,
            client_metadata=client_metadata(redirect_port),
            storage=storage,
            redirect_handler=redirect_handler if interactive else self._no_person,
            callback_handler=callback_handler,
            client_metadata_url=client_metadata_url,
        )
        self.title = config.display_title
        self.interactive = interactive
        self._name = config.name
        self._storage = storage
        self._fence_dir = fence_dir
        self._refreshed = False

    def finish_sign_in(self) -> None:
        """After a successful sign-in, behave as a background provider."""
        self.interactive = False
        self.context.redirect_handler = self._no_person

    async def _no_person(self, _url: str) -> None:
        # Backstop: the wrapper stops a background 401 before the SDK gets here.
        raise NeedsSignIn(needs_sign_in_notice(self.title))

    async def _initialize(self) -> None:
        await super()._initialize()
        expires_at, metadata, _issuer = self._storage.load_extras()
        if expires_at is not None and self.context.current_tokens is not None:
            self.context.token_expiry_time = expires_at - REFRESH_SKEW_S
        if metadata is not None and self.context.oauth_metadata is None:
            self.context.oauth_metadata = metadata

    def _needs_refresh(self) -> bool:
        return (
            self.context.current_tokens is not None
            and not self.context.is_token_valid()
            and self.context.can_refresh_token()
        )

    async def _auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if not self._initialized:
            await self._initialize()
        if self._needs_refresh():
            async with self.context.lock:
                steps = self._refresh_steps()
                try:
                    outgoing = await steps.__anext__()
                    while True:
                        outgoing = await steps.asend((yield outgoing))
                except StopAsyncIteration:
                    pass
                finally:
                    await steps.aclose()

        inner = super()._auth_flow(request)
        tried_refresh = False
        try:
            outgoing = await inner.__anext__()
            while True:
                response = yield outgoing
                if outgoing is request and response.status_code == 401:
                    if not tried_refresh and self.context.can_refresh_token():
                        tried_refresh = True
                        steps = self._refresh_steps()
                        try:
                            step = await steps.__anext__()
                            while True:
                                step = await steps.asend((yield step))
                        except StopAsyncIteration:
                            pass
                        finally:
                            await steps.aclose()
                        if self._refreshed:
                            self._add_auth_header(request)
                            response = yield request
                    if response.status_code == 401 and not self.interactive:
                        raise NeedsSignIn(needs_sign_in_notice(self.title))
                outgoing = await inner.asend(response)
        except StopAsyncIteration:
            return
        finally:
            await inner.aclose()

    async def _refresh_steps(self) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        """Refresh once, unless another process already did it."""
        self._refreshed = False
        async with refresh_fence(self._fence_dir, self._name):
            if await self._adopt_peer_tokens():
                self._refreshed = True
                return
            if not self.context.can_refresh_token():
                return
            response = yield await self._refresh_token()
            self._refreshed = await self._handle_refresh_response(response)

    async def _adopt_peer_tokens(self) -> bool:
        """Take tokens another process refreshed since this one loaded them."""
        stored = await self._storage.get_tokens()
        current = self.context.current_tokens
        if (
            stored is None
            or current is None
            or stored.access_token == current.access_token
        ):
            return False
        expires_at, _metadata, _issuer = self._storage.load_extras()
        self.context.current_tokens = stored
        self.context.token_expiry_time = (
            expires_at - REFRESH_SKEW_S if expires_at is not None else None
        )
        return self.context.is_token_valid()


def background_provider(
    config: ConnectorConfig, store: CredentialStore
) -> AldwinOAuthProvider:
    """The provider a connector uses when no person is there to sign in."""
    return AldwinOAuthProvider(
        config=config,
        storage=CredentialTokenStorage(store, config.name),
        fence_dir=store.directory,
        interactive=False,
    )
