"""The host's connection to the browser: one WebSocket peer that can come and go."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from starlette.websockets import WebSocketDisconnect

from aldwin.protocol import HOST_EVENT_RE, ErrorEvent, Event, Request, parse_request

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

NewConversationHandler = Callable[[], Awaitable[None]]

WEB_CHANNEL = "web"

CLOSE_GOING_AWAY = 1001
CLOSE_UNSUPPORTED_DATA = 1003
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_REPLACED = 4000
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_ALREADY_ATTACHED = 4409
REASON_REPLACED = "replaced"
REASON_ALREADY_ATTACHED = "already_attached"

BAD_REQUEST_MESSAGE = "Aldwin could not read that message as a request."


class WebSocketTransport:
    """``HostTransport`` for ``aldwin web``: at most one live browser tab.

    A tab closing only detaches it. The transport stays open, so the host
    keeps serving and pending approvals wait for the tab to come back. Only
    ``close()``, at shutdown, makes ``receive_request`` return ``None``.
    """

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[Request | None] = asyncio.Queue()
        self._peer: WebSocket | None = None
        self._closed = False
        self._attach_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._new_conversation: NewConversationHandler | None = None

    @property
    def closed(self) -> bool:
        """Return ``True`` once the transport has been shut down for good."""
        return self._closed

    @property
    def attached(self) -> bool:
        """Return ``True`` while a tab is connected."""
        return self._peer is not None and not self._closed

    async def attach(self, websocket: WebSocket, *, take_over: bool = False) -> bool:
        """Make ``websocket`` the live tab; return whether it was attached.

        If another tab is live, the new one is closed with 4409
        ``already_attached`` unless ``take_over`` is set, in which case the old
        tab is closed with 4000 ``replaced``.
        """
        async with self._attach_lock:
            if self._closed:
                await _close_quietly(websocket, CLOSE_GOING_AWAY, "")
                return False
            previous = self._peer
            if previous is not None and not take_over:
                await _close_quietly(
                    websocket, CLOSE_ALREADY_ATTACHED, REASON_ALREADY_ATTACHED
                )
                return False
            self._peer = websocket
        if previous is not None:
            await _close_quietly(previous, CLOSE_REPLACED, REASON_REPLACED)
        return True

    def detach(self, websocket: WebSocket) -> None:
        """Forget ``websocket`` if it is still the live tab; never closes."""
        if self._peer is websocket:
            self._peer = None

    async def handle_text(self, websocket: WebSocket, text: str) -> None:
        """Queue one JSON request from ``websocket`` for the host."""
        if websocket is not self._peer:
            if self._peer is not None:
                # Another tab took over; tell this one instead of ignoring it.
                await _close_quietly(websocket, CLOSE_REPLACED, REASON_REPLACED)
            return
        try:
            request = parse_request(text)
        except ValueError:
            error = ErrorEvent(
                re=_request_id(text), code="protocol", message=BAD_REQUEST_MESSAGE
            )
            await self._send_to(websocket, error)
            return
        # The browser decides nothing about where its requests came from.
        await self._inbound.put(
            request.model_copy(update={"channel": WEB_CHANNEL, "sender": WEB_CHANNEL})
        )

    async def receive_request(self) -> Request | None:
        """Await the next request; ``None`` only after ``close()``."""
        return await self._inbound.get()

    async def send_event(self, event: Event) -> None:
        """Send ``event`` to the live tab, or drop it when none is attached."""
        peer = self._peer
        if self._closed or peer is None:
            return
        await self._send_to(peer, event)

    def set_new_conversation_handler(self, handler: NewConversationHandler) -> None:
        """Keep the host's callback; the browser starts chats with ``sessions.new``."""
        self._new_conversation = handler

    async def close(self) -> None:
        """Shut down for good: wake the host with ``None`` and close the tab."""
        if self._closed:
            return
        self._closed = True
        peer, self._peer = self._peer, None
        await self._inbound.put(None)
        if peer is not None:
            await _close_quietly(peer, CLOSE_GOING_AWAY, "")

    async def _send_to(self, websocket: WebSocket, event: Event) -> None:
        payload = event.model_copy(update={"channel": WEB_CHANNEL}).model_dump_json()
        try:
            async with self._send_lock:
                await websocket.send_text(payload)
        except (WebSocketDisconnect, RuntimeError, OSError):
            self.detach(websocket)


def _request_id(text: str) -> str:
    try:
        raw: Any = json.loads(text)
    except ValueError:
        return HOST_EVENT_RE
    value = raw.get("id") if isinstance(raw, dict) else None
    if isinstance(value, str) and 0 < len(value) <= 100:
        return value
    return HOST_EVENT_RE


async def _close_quietly(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
