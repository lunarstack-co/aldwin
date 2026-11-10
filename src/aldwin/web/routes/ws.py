"""``POST /v1/ws-ticket`` and the ``/v1/ws`` WebSocket that carries the protocol."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from aldwin.web.auth import Authenticator
from aldwin.web.runtime import WebHostRuntime
from aldwin.web.ws_transport import (
    CLOSE_FORBIDDEN,
    CLOSE_TRY_AGAIN_LATER,
    CLOSE_UNAUTHORIZED,
    CLOSE_UNSUPPORTED_DATA,
)

WS_PATH = "/ws"
HOST_NOT_READY = "host_not_ready"
HOST_NOT_READY_MESSAGE = "Aldwin is not ready yet. Finish setup, then try again."
BAD_TICKET_MESSAGE = "This connection needs a fresh ticket from POST /v1/ws-ticket."
FOREIGN_PAGE_MESSAGE = "This connection did not come from the Aldwin page."
_TRUE = frozenset({"1", "true", "yes"})


def ws_router(runtime: WebHostRuntime, authenticator: Authenticator) -> APIRouter:
    """Build the ticket route and the WebSocket route for ``runtime``."""
    router = APIRouter()

    # Async so minting and spending tickets share the event loop, never a thread.
    @router.post("/ws-ticket", response_model=None)
    async def mint_ticket() -> dict[str, Any] | JSONResponse:
        if not runtime.ready:
            return _not_ready_response()
        return {
            "ticket": runtime.tickets.mint(),
            "expires_in": int(runtime.tickets.ttl),
        }

    @router.websocket(WS_PATH)
    async def socket(
        websocket: WebSocket, ticket: str | None = None, take_over: str | None = None
    ) -> None:
        headers = websocket.headers
        origin = headers.get("origin")
        # A WebSocket upgrade has no CORS, so the Origin must be present and ours.
        if not (
            origin is not None
            and authenticator.host_ok(headers.get("host"))
            and authenticator.origin_ok(origin)
            and authenticator.fetch_site_ok(headers.get("sec-fetch-site"))
        ):
            await _refuse(websocket, 403, CLOSE_FORBIDDEN, FOREIGN_PAGE_MESSAGE)
            return
        if not runtime.ready:
            await _refuse(websocket, 503, CLOSE_TRY_AGAIN_LATER, HOST_NOT_READY_MESSAGE)
            return
        if not runtime.tickets.consume(ticket):
            await _refuse(websocket, 401, CLOSE_UNAUTHORIZED, BAD_TICKET_MESSAGE)
            return

        await websocket.accept()
        transport = runtime.transport
        wants_take_over = (take_over or "").lower() in _TRUE
        if not await transport.attach(websocket, take_over=wants_take_over):
            return
        try:
            await _pump(websocket, runtime)
        finally:
            transport.detach(websocket)

    return router


async def _pump(websocket: WebSocket, runtime: WebHostRuntime) -> None:
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is None:
                await websocket.close(CLOSE_UNSUPPORTED_DATA, "text frames only")
                return
            await runtime.transport.handle_text(websocket, text)
    except (WebSocketDisconnect, RuntimeError):
        return


async def _refuse(websocket: WebSocket, status: int, code: int, message: str) -> None:
    """Refuse before accepting: an HTTP status if the server can, else a close."""
    if "websocket.http.response" in websocket.scope.get("extensions", {}):
        await websocket.send_denial_response(
            JSONResponse({"detail": message}, status_code=status)
        )
    else:
        await websocket.close(code=code, reason=message)


def _not_ready_response() -> JSONResponse:
    return JSONResponse(
        {"detail": HOST_NOT_READY_MESSAGE, "error": HOST_NOT_READY}, status_code=503
    )
