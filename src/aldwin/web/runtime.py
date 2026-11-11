"""What ``aldwin web`` shares between its routes and the host it serves."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

import aldwin.client as client
from aldwin.web.tickets import DEFAULT_TICKET_TTL, TicketStore
from aldwin.web.ws_transport import WebSocketTransport

logger = logging.getLogger("aldwin.web")


class _Host(Protocol):
    async def stop_host(self) -> None: ...


class WebHostRuntime:
    """The WebSocket transport, its tickets, and the host serving it.

    The host only starts once a model can run (a working key or a local
    model). Until then tickets and sockets are refused, and the page shows
    setup instead.
    """

    def __init__(
        self,
        *,
        home: Path,
        workspace: Path | str | None = None,
        ticket_ttl: float = DEFAULT_TICKET_TTL,
    ) -> None:
        self.transport = WebSocketTransport()
        self.tickets = TicketStore(ttl=ticket_ttl)
        self._home = home
        self._workspace = workspace
        self._host: _Host | None = None
        self._ready = False
        self._start_lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        """Return ``True`` while a host is reading from ``transport``."""
        return self._ready and not self.transport.closed

    def mark_ready(self) -> None:
        """Record that a host has started on ``transport``."""
        self._ready = True

    async def start_host(self) -> bool:
        """Start the host if a model can run; return whether one is serving.

        Safe to call after every setup change: it starts at most one host and
        does nothing while keys are missing.
        """
        async with self._start_lock:
            if self._host is not None or self._ready:
                return True
            if self.transport.closed:
                return False
            try:
                if not client.keys_gate_ok(self._home):
                    return False
            except client.SettingsError as exc:
                logger.warning("Could not read settings: %s", exc)
                return False
            try:
                self._host = await client.start_host(
                    self.transport, home=self._home, workspace=self._workspace
                )
            except Exception:
                # The page stays on setup and the status shows no host.
                logger.exception("Could not start Aldwin for the browser")
                return False
            self._ready = True
            return True

    async def shutdown(self) -> None:
        """Stop the host, if any, and close the transport."""
        self._ready = False
        host, self._host = self._host, None
        if host is not None:
            await host.stop_host()
        await self.transport.close()
