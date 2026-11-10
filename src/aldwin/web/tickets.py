"""One-time tickets that let the signed-in page open the ``/v1/ws`` WebSocket.

Browsers do not apply CORS to WebSocket upgrades, so the socket cannot rely on
the cookie alone: the page first asks ``POST /v1/ws-ticket`` (cookie and
same-origin checks apply) and hands the ticket to the upgrade, which spends it.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable

DEFAULT_TICKET_TTL = 30.0


class TicketStore:
    """Short-lived tickets held in this process only; each opens one socket."""

    def __init__(
        self,
        ttl: float = DEFAULT_TICKET_TTL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = ttl
        self._clock = clock
        self._expires: dict[str, float] = {}

    def mint(self) -> str:
        """Return a new ticket that expires after ``ttl`` seconds."""
        now = self._clock()
        for stale in [t for t, at in self._expires.items() if at <= now]:
            del self._expires[stale]
        ticket = secrets.token_urlsafe(32)
        self._expires[ticket] = now + self.ttl
        return ticket

    def consume(self, ticket: str | None) -> bool:
        """Spend ``ticket``; ``True`` only the first time and before it expires."""
        if not ticket:
            return False
        expires_at = self._expires.pop(ticket, None)
        return expires_at is not None and expires_at > self._clock()
