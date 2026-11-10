"""Host transport double whose peer can detach and reattach, like a browser tab."""

from __future__ import annotations

import asyncio

from aldwin.host import NewConversationHandler
from aldwin.protocol import Event, Request


class FakeHostTransport:
    """`HostTransport` for tests: requests in, events out, peer can come and go.

    Detaching never closes the transport or wakes `receive_request` with
    `None`; only `close()` does. Events sent while detached are dropped and
    counted, as a real socket drop would lose them.
    """

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[Request | None] = asyncio.Queue()
        self._outbound: asyncio.Queue[Event] = asyncio.Queue()
        self._attached = True
        self._closed = False
        self.dropped_events: list[Event] = []
        self.new_conversation_handler: NewConversationHandler | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def attached(self) -> bool:
        return self._attached and not self._closed

    def detach(self) -> None:
        """Simulate the peer disconnecting without closing the transport."""
        self._attached = False

    def attach(self) -> None:
        """Simulate the peer reconnecting."""
        self._attached = True

    async def push(self, request: Request) -> None:
        """Deliver a request from the peer to the host."""
        await self._inbound.put(request)

    async def next_event(self, timeout: float = 5.0) -> Event:
        """Return the next event the host sent while attached."""
        return await asyncio.wait_for(self._outbound.get(), timeout)

    async def receive_request(self) -> Request | None:
        return await self._inbound.get()

    async def send_event(self, event: Event) -> None:
        if self._closed:
            return
        if not self._attached:
            self.dropped_events.append(event)
            return
        await self._outbound.put(event)

    def set_new_conversation_handler(self, handler: NewConversationHandler) -> None:
        self.new_conversation_handler = handler

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._inbound.put(None)
