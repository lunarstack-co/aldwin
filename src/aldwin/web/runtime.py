"""What ``aldwin web`` shares between its routes and the host it serves."""

from __future__ import annotations

from aldwin.web.tickets import DEFAULT_TICKET_TTL, TicketStore
from aldwin.web.ws_transport import WebSocketTransport


class WebHostRuntime:
    """The WebSocket transport, its tickets, and whether a host is serving it.

    Tickets and sockets are refused until ``mark_ready()``: the host is only
    started once a model key works, so there is nothing to talk to before that.
    """

    def __init__(self, *, ticket_ttl: float = DEFAULT_TICKET_TTL) -> None:
        self.transport = WebSocketTransport()
        self.tickets = TicketStore(ttl=ticket_ttl)
        self._ready = False

    @property
    def ready(self) -> bool:
        """Return ``True`` while a host is reading from ``transport``."""
        return self._ready and not self.transport.closed

    def mark_ready(self) -> None:
        """Record that a host has started on ``transport``."""
        self._ready = True

    async def shutdown(self) -> None:
        """Stop accepting tabs and close the transport, which stops the host."""
        self._ready = False
        await self.transport.close()
