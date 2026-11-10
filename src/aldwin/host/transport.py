"""Host-side transport contract that every door's connection implements."""

from __future__ import annotations

from typing import Protocol

from aldwin.host.in_process import NewConversationHandler
from aldwin.protocol import Event, Request


class HostTransport(Protocol):
    """The host's view of one door connection.

    `closed` is permanent and only becomes true when the host shuts the
    transport down. `attached` reports whether a peer is connected right now.
    A peer that drops and reconnects, such as a browser tab, flips `attached`
    and never closes the transport, so the host keeps serving and pending
    approvals survive the gap.
    """

    @property
    def closed(self) -> bool:
        """Return `True` once the transport has been shut down for good."""
        ...

    @property
    def attached(self) -> bool:
        """Return `True` while a peer is connected and can answer approvals."""
        ...

    async def receive_request(self) -> Request | None:
        """Await the next request.

        Returns `None` only when the transport is closed for good. A peer
        disconnecting must not return `None`: the host treats it as shutdown,
        stops serving and denies every pending approval.
        """
        ...

    async def send_event(self, event: Event) -> None:
        """Deliver an event to the attached peer.

        With no peer attached the event is dropped, not queued: a door that
        reattaches re-fetches history instead of replaying what it missed.
        """
        ...

    def set_new_conversation_handler(self, handler: NewConversationHandler) -> None:
        """Register the host callback a door uses to start a new conversation."""
        ...

    async def close(self) -> None:
        """Shut the transport down; `receive_request` then returns `None`."""
        ...
