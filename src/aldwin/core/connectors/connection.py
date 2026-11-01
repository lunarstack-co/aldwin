"""One long-lived MCP session per connector, plus the dry-run probe for adding one.

`mcp` is imported inside functions: importing it costs about 200 ms, which must
not land on Aldwin's startup path. (`httpx2` is already loaded by Agentling.)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Generator, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx2

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.credentials import CredentialStore, resolve_bearer_token
from aldwin.core.connectors.errors import (
    ConnectorAuthRejected,
    ConnectorAuthRequired,
    ConnectorError,
    ConnectorNotMcp,
    ConnectorSessionLost,
    ConnectorUnreachable,
    SignInError,
)
from aldwin.core.connectors.mcp_tool import RemoteToolSpec, ToolCallOutcome

logger = logging.getLogger("aldwin.core.connectors.connection")

OPEN_TIMEOUT_S = 30.0
CALL_TIMEOUT_S = 120.0
CLOSE_TIMEOUT_S = 5.0

# Returns what `mcp.Client` connects to: a transport, or an in-process server in tests.
TransportFactory = Callable[[httpx2.AsyncClient], Any]


class BearerAuth(httpx2.Auth):
    """Sends `Authorization: Bearer <token>` on each request.

    An `Auth` (not a default header) keeps the token out of the client's repr
    and debug logs, and is the same seam OAuth plugs into later.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request

    def __repr__(self) -> str:
        return "BearerAuth(token=<hidden>)"


def build_auth(
    config: ConnectorConfig, store: CredentialStore, environ: Mapping[str, str]
) -> httpx2.Auth | None:
    """The auth to send for `config`, or `None` when it has no token."""
    if config.auth == "oauth":
        from aldwin.core.connectors.oauth import background_provider

        return background_provider(config, store)
    token = resolve_bearer_token(config, store, environ)
    if token is None:
        return None
    return BearerAuth(token)


class McpConnection:
    """Owns one `mcp.Client` in its own task.

    The SDK's task groups must be entered and exited in the same task, so a
    dedicated owner task opens the session, waits for `close()`, and reports a
    dropped session through `on_lost`.
    """

    def __init__(
        self,
        config: ConnectorConfig,
        *,
        auth: httpx2.Auth | None,
        on_tools_changed: Callable[[], Awaitable[None]],
        on_lost: Callable[[ConnectorError], Awaitable[None]],
        transport_factory: TransportFactory | None = None,
        protocol_mode: str = "auto",
    ) -> None:
        """`protocol_mode` is the SDK's connect mode; tests pin "legacy" to cover
        2025-era servers, which push `list_changed` instead of using `listen`.
        """
        self.config = config
        self._auth = auth
        self._protocol_mode = protocol_mode
        self._on_tools_changed = on_tools_changed
        self._on_lost = on_lost
        self._transport_factory = transport_factory
        self._client: Any = None
        self._owner: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[list[RemoteToolSpec]] | None = None
        self._closing = asyncio.Event()
        self._close_requested = False
        self._lost: ConnectorError | None = None
        self._last_response: tuple[int, str] | None = None
        self._side_tasks: set[asyncio.Task[Any]] = set()

    @property
    def host(self) -> str:
        return urlsplit(self.config.url).netloc or self.config.url

    async def open(self, *, timeout: float = OPEN_TIMEOUT_S) -> list[RemoteToolSpec]:
        """Connect, list every tool, and keep the session open."""
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._owner = asyncio.create_task(self._own(), name=f"mcp:{self.config.name}")
        try:
            async with asyncio.timeout(timeout):
                return await asyncio.shield(self._ready)
        except TimeoutError:
            # The owner is stuck mid-connect and won't see `close`; don't wait long.
            await self.close(timeout=1.0)
            raise ConnectorUnreachable(
                self.host, f"no answer after {timeout:g}s"
            ) from None
        except ConnectorError:
            await self.close()
            raise

    async def list_tools(self) -> list[RemoteToolSpec]:
        """Every tool on the server, all pages, never from a cache."""
        try:
            return await _list_all(self._require_client())
        except Exception as exc:
            raise self._lose(exc) from exc

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, timeout: float = CALL_TIMEOUT_S
    ) -> ToolCallOutcome:
        """Call `name`. A tool-level failure is `is_error`, not an exception."""
        client = self._require_client()
        try:
            async with asyncio.timeout(timeout):
                result = await client.call_tool(
                    name, arguments, read_timeout_seconds=timeout
                )
        except TimeoutError:
            raise
        except Exception as exc:
            raise self._lose(exc) from exc
        return _outcome(result)

    async def close(self, *, timeout: float = CLOSE_TIMEOUT_S) -> None:
        """Close the session within `timeout`; a session that hangs is abandoned."""
        self._close_requested = True
        self._closing.set()
        owner = self._owner
        if owner is None or owner.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(owner), timeout)
        except TimeoutError:
            owner.cancel()
            logger.warning(
                "connector %s did not close within %gs; abandoned",
                self.config.name,
                timeout,
            )
        except Exception:
            pass

    async def _own(self) -> None:
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        from mcp_types import Implementation

        from aldwin import __version__
        from aldwin.core.tools import USER_AGENT

        assert self._ready is not None
        try:
            async with httpx2.AsyncClient(
                timeout=httpx2.Timeout(30.0, read=300.0),
                headers={"user-agent": USER_AGENT},
                auth=self._auth,
                event_hooks={"response": [self._record_response]},
            ) as http:
                target = (
                    self._transport_factory(http)
                    if self._transport_factory is not None
                    else streamable_http_client(self.config.url, http_client=http)
                )
                async with Client(
                    target,
                    mode=self._protocol_mode,
                    cache=None,
                    message_handler=self._on_message,
                    client_info=Implementation(name="aldwin", version=__version__),
                ) as client:
                    self._client = client
                    tools = await _list_all(client)
                    subscribed = asyncio.get_running_loop().create_future()
                    listener = asyncio.create_task(self._listen(client, subscribed))
                    # Ready only once changes can't be missed between list and listen.
                    await asyncio.wait(
                        {subscribed, listener}, return_when="FIRST_COMPLETED"
                    )
                    self._ready.set_result(tools)
                    try:
                        await self._closing.wait()
                    finally:
                        listener.cancel()
                        with contextlib.suppress(BaseException):
                            await listener
        except asyncio.CancelledError:
            if not self._ready.done():
                self._ready.set_exception(ConnectorUnreachable(self.host, "cancelled"))
            raise
        except BaseException as exc:
            error = self._classify(exc)
            if not self._ready.done():
                self._ready.set_exception(error)
            elif self._lost is None and not self._close_requested:
                self._lost = error
        finally:
            self._client = None
            if self._ready.done() and not self._ready.cancelled():
                self._ready.exception()  # mark retrieved when open() gave up waiting
        if self._lost is not None and not self._close_requested:
            await self._on_lost(self._lost)

    async def _listen(self, client: Any, subscribed: asyncio.Future[None]) -> None:
        """Watch `tools/list_changed` on 2026-07-28 sessions (older ones push it).

        `subscribed` resolves once the server has acknowledged the stream, or
        straight away when the session is too old to support it.
        """
        from mcp.client.subscriptions import ListenNotSupportedError

        try:
            async with client.listen(tools_list_changed=True) as subscription:
                subscribed.set_result(None)
                async for _event in subscription:
                    self._spawn(self._on_tools_changed())
        except ListenNotSupportedError:
            subscribed.set_result(None)
        except Exception as exc:
            self._lose(exc)

    async def _on_message(self, message: Any) -> None:
        from mcp_types import ToolListChangedNotification

        if isinstance(message, Exception):
            self._lose(message)
        elif isinstance(message, ToolListChangedNotification):
            # Re-listing waits on this receive loop, so it runs in its own task.
            self._spawn(self._on_tools_changed())

    async def _record_response(self, response: httpx2.Response) -> None:
        self._last_response = (
            response.status_code,
            response.headers.get("content-type", ""),
        )

    def _spawn(self, coro: Awaitable[None]) -> None:
        task: asyncio.Task[Any] = asyncio.ensure_future(coro)
        self._side_tasks.add(task)
        task.add_done_callback(self._side_tasks.discard)

    def _require_client(self) -> Any:
        if self._client is None:
            raise ConnectorSessionLost(f"{self.config.display_title} is not connected.")
        return self._client

    def _lose(self, exc: BaseException) -> ConnectorError:
        """Record a dropped session and end the owner task, which reports it."""
        if self._lost is None:
            self._lost = ConnectorSessionLost(
                f"{self.config.display_title} disconnected; try again shortly."
            )
            logger.info("connector %s lost: %r", self.config.name, exc)
            self._closing.set()
        return self._lost

    def _classify(self, exc: BaseException) -> ConnectorError:
        """Turn whatever the SDK raised into one user-facing error."""
        from mcp.client.auth.exceptions import OAuthFlowError, OAuthRegistrationError
        from mcp.shared.exceptions import MCPError

        from aldwin.core.connectors.oauth_messages import (
            issuer_mismatch,
            registration_refused,
        )

        leaves = list(_leaves(exc))
        for leaf in leaves:
            if isinstance(leaf, ConnectorError):
                return leaf
        title = self.config.display_title
        for leaf in leaves:
            if isinstance(leaf, OAuthRegistrationError):
                return SignInError(registration_refused(title, self.config.name))
            if isinstance(leaf, OAuthFlowError):
                return SignInError(issuer_mismatch(title))
        status, content_type = self._last_response or (0, "")
        if status == 401:
            if self._auth is not None:
                return ConnectorAuthRejected()
            return ConnectorAuthRequired(self.host)
        media = content_type.split(";")[0].strip().lower()
        if (
            status == 200
            and media
            and media
            not in (
                "application/json",
                "text/event-stream",
            )
        ):
            return ConnectorNotMcp(media)
        for leaf in leaves:
            if isinstance(leaf, MCPError) and "Session terminated" in str(leaf):
                return ConnectorSessionLost(
                    f"{self.config.display_title} disconnected; try again shortly."
                )
        reason = next(
            (str(leaf) for leaf in leaves if str(leaf)),
            type(leaves[0]).__name__ if leaves else "unknown error",
        )
        return ConnectorUnreachable(self.host, reason.rstrip("."))


async def probe_connector(
    config: ConnectorConfig,
    auth: httpx2.Auth | None,
    *,
    transport_factory: TransportFactory | None = None,
    timeout: float = OPEN_TIMEOUT_S,
) -> list[RemoteToolSpec]:
    """Connect once, list the tools, and disconnect (the add flow's dry run)."""

    async def _noop() -> None:
        return None

    async def _ignore(_error: ConnectorError) -> None:
        return None

    connection = McpConnection(
        config,
        auth=auth,
        on_tools_changed=_noop,
        on_lost=_ignore,
        transport_factory=transport_factory,
    )
    try:
        return await connection.open(timeout=timeout)
    finally:
        await connection.close()


async def _list_all(client: Any) -> list[RemoteToolSpec]:
    specs: list[RemoteToolSpec] = []
    cursor: str | None = None
    while True:
        page = await client.list_tools(cursor=cursor)
        specs.extend(_to_spec(tool) for tool in page.tools)
        cursor = page.next_cursor
        if cursor is None:
            return specs


def _to_spec(tool: Any) -> RemoteToolSpec:
    hints = tool.annotations
    return RemoteToolSpec(
        name=tool.name,
        title=tool.title or (hints.title if hints is not None else None),
        description=tool.description or "",
        input_schema=dict(tool.input_schema or {}),
        read_only_hint=hints.read_only_hint if hints is not None else None,
        destructive_hint=hints.destructive_hint if hints is not None else None,
    )


def _outcome(result: Any) -> ToolCallOutcome:
    from mcp_types import (
        AudioContent,
        BlobResourceContents,
        EmbeddedResource,
        ImageContent,
        ResourceLink,
        TextContent,
        TextResourceContents,
    )

    texts: list[str] = []
    binaries: list[tuple[str, bytes]] = []
    for block in result.content:
        if isinstance(block, TextContent):
            texts.append(block.text)
        elif isinstance(block, (ImageContent, AudioContent)):
            binaries.append((block.mime_type, base64.b64decode(block.data)))
        elif isinstance(block, ResourceLink):
            texts.append(f"resource: {block.uri}")
        elif isinstance(block, EmbeddedResource):
            res = block.resource
            if isinstance(res, TextResourceContents):
                texts.append(res.text)
            elif isinstance(res, BlobResourceContents):
                mime = res.mime_type or "application/octet-stream"
                binaries.append((mime, base64.b64decode(res.blob)))
    if not texts and not binaries and result.structured_content is not None:
        texts.append(json.dumps(result.structured_content, ensure_ascii=False))
    return ToolCallOutcome(
        text_parts=texts, binary_parts=binaries, is_error=bool(result.is_error)
    )


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    return [exc]


class NeedsSignInCaller:
    """Stands in for a connection while a connector needs sign-in.

    The tools stay registered, so the model gets a clear sentence instead of an
    unknown-tool error, and nothing touches the network.
    """

    def __init__(self, title: str) -> None:
        self._title = title

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, timeout: float = CALL_TIMEOUT_S
    ) -> ToolCallOutcome:
        from aldwin.core.connectors.oauth_messages import needs_sign_in_tool_error

        return ToolCallOutcome(
            text_parts=[needs_sign_in_tool_error(self._title)], is_error=True
        )
