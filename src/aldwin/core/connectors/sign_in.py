"""One interactive browser sign-in, from discovery to a saved credential.

Nothing is written unless the token exchange and the first tool listing both
succeed, so a sign-in that fails leaves no partial credentials file.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.connection import McpConnection
from aldwin.core.connectors.credentials import CredentialStore
from aldwin.core.connectors.errors import SignInError, SignInTimedOut
from aldwin.core.connectors.mcp_tool import RemoteToolSpec
from aldwin.core.connectors.oauth import (
    AldwinOAuthProvider,
    ClientIdentity,
    choose_client_identity,
    client_metadata,
    discover,
    require_s256,
)
from aldwin.core.connectors.oauth_callback import (
    CallbackResult,
    LoopbackCallback,
    parse_redirect_url,
)
from aldwin.core.connectors.oauth_messages import (
    CALLBACK_PORTS,
    SIGN_IN_TIMEOUT_S,
    nothing_waiting,
    sign_in_declined,
)
from aldwin.core.connectors.token_storage import CredentialTokenStorage

# `open()` must outlast the person in the browser.
OPEN_TIMEOUT_S = SIGN_IN_TIMEOUT_S + 30


@dataclass
class SignInWait:
    """A sign-in waiting for the browser to come back, or for a pasted address."""

    name: str
    expected_state: str | None = None
    replies: asyncio.Queue[CallbackResult] = field(default_factory=asyncio.Queue)

    def paste(self, url: str) -> None:
        """Accept a pasted address; raises `SignInError` for a bad one."""
        if self.expected_state is None:
            raise SignInError(nothing_waiting(self.name))
        self.replies.put_nowait(
            parse_redirect_url(url, expected_state=self.expected_state)
        )


@dataclass(frozen=True)
class SignedIn:
    connection: McpConnection
    specs: list[RemoteToolSpec]


async def sign_in(
    config: ConnectorConfig,
    store: CredentialStore,
    *,
    cimd_url: str,
    no_browser: bool,
    wait: SignInWait,
    on_auth_url: Callable[[str], Awaitable[None]],
    connect: Callable[[AldwinOAuthProvider], McpConnection],
) -> SignedIn | None:
    """Sign in through the browser and return the open connection.

    Returns `None` when the server turns out to need no sign-in.
    """
    async with httpx2.AsyncClient() as http:
        found = await discover(config, client=http)
    if found is None:
        return None
    identity = choose_client_identity(config, found.asm, cimd_url)
    require_s256(config.display_title, found.asm)

    callback: LoopbackCallback | None = None
    try:
        callback = await LoopbackCallback.bind()
    except SignInError:
        if not no_browser:
            raise
        # Paste mode needs no listener; the redirect just has to be registered.
    port = callback.port if callback is not None else CALLBACK_PORTS[0]

    storage = CredentialTokenStorage(
        store,
        config.name,
        staged=True,
        client_info=_seed_client_info(config, store, identity, found.issuer, port),
    )

    async def redirect(url: str) -> None:
        state = parse_qs(urlsplit(url).query).get("state", [""])[0]
        wait.expected_state = state
        if callback is not None:
            callback.expect_state(state)
        await on_auth_url(url)

    async def come_back() -> AuthorizationCodeResult:
        result = await _first_result(callback, wait)
        if result.error is not None:
            raise SignInError(sign_in_declined(config.display_title))
        return AuthorizationCodeResult(
            code=result.code or "", state=result.state, iss=result.iss
        )

    provider = AldwinOAuthProvider(
        config=config,
        storage=storage,
        fence_dir=store.directory,
        interactive=True,
        redirect_port=port,
        redirect_handler=redirect,
        callback_handler=come_back,
        client_metadata_url=cimd_url if identity.kind == "cimd" else None,
    )
    connection = connect(provider)
    try:
        specs = await connection.open(timeout=OPEN_TIMEOUT_S)
        storage.commit(issuer=found.issuer, metadata=found.asm)
        provider.finish_sign_in()
    except BaseException:
        await connection.close()
        raise
    finally:
        if callback is not None:
            await callback.close()
    return SignedIn(connection=connection, specs=specs)


async def _first_result(
    callback: LoopbackCallback | None, wait: SignInWait
) -> CallbackResult:
    """Whichever comes first: the browser's return trip or a pasted address."""
    waiters = [asyncio.ensure_future(wait.replies.get())]
    if callback is not None:
        waiters.append(asyncio.ensure_future(callback.result()))
    try:
        done, _pending = await asyncio.wait(
            waiters, timeout=SIGN_IN_TIMEOUT_S, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            raise SignInTimedOut()
        return next(iter(done)).result()
    finally:
        for waiter in waiters:
            waiter.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.gather(*waiters, return_exceptions=True)


def _seed_client_info(
    config: ConnectorConfig,
    store: CredentialStore,
    identity: ClientIdentity,
    issuer: str,
    port: int,
) -> OAuthClientInformationFull | None:
    """Reuse a client the user already has, so a re-sign-in never registers again."""
    if identity.kind == "preregistered":
        return OAuthClientInformationFull(
            client_id=identity.client_id or "",
            redirect_uris=client_metadata(port).redirect_uris,
            token_endpoint_auth_method="none",
        )
    if identity.kind == "dcr":
        existing = store.get(config.name) or {}
        if existing.get("kind") == "oauth" and existing.get("issuer") == issuer:
            raw = existing.get("client_info")
            if raw:
                return OAuthClientInformationFull.model_validate(raw)
    return None
