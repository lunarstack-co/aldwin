"""The browser's return trip after sign-in: a one-request listener on
`127.0.0.1`, and parsing for an address the user pastes instead.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from aldwin.core.connectors.errors import SignInError
from aldwin.core.connectors.oauth_messages import (
    CALLBACK_PATH,
    CALLBACK_PORTS,
    PASTE_NO_CODE,
    PASTE_WRONG_STATE,
    ports_busy,
)

_MAX_REQUEST_BYTES = 8 * 1024
_READ_TIMEOUT_S = 5.0
_PAGES = {
    "ok": "You're signed in. You can close this tab.",
    "declined": "Sign-in was declined. You can close this tab.",
    "stale": "This sign-in link has expired. Start again from Aldwin.",
}


@dataclass(frozen=True)
class CallbackResult:
    code: str | None
    state: str | None
    iss: str | None = None
    error: str | None = None


def _result_from_query(query: str) -> CallbackResult:
    params = {k: v[0] for k, v in parse_qs(query).items()}
    return CallbackResult(
        code=params.get("code"),
        state=params.get("state"),
        iss=params.get("iss"),
        error=params.get("error"),
    )


def parse_redirect_url(url: str, *, expected_state: str) -> CallbackResult:
    """Read `code` and `state` from an address the user pasted."""
    result = _result_from_query(urlsplit(url.strip()).query)
    if result.state != expected_state:
        if result.state is None and result.code is None:
            raise SignInError(PASTE_NO_CODE)
        raise SignInError(PASTE_WRONG_STATE)
    if result.code is None and result.error is None:
        raise SignInError(PASTE_NO_CODE)
    return result


class LoopbackCallback:
    """Waits for exactly one browser request carrying the expected `state`."""

    def __init__(self, server: asyncio.Server, port: int) -> None:
        self._server = server
        self.port = port
        self._expected: str | None = None
        self._result: asyncio.Future[CallbackResult] = (
            asyncio.get_running_loop().create_future()
        )

    @classmethod
    async def bind(cls, ports: Sequence[int] = CALLBACK_PORTS) -> LoopbackCallback:
        holder: list[LoopbackCallback] = []

        async def handle(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await holder[0]._handle(reader, writer)

        for port in ports:
            try:
                # Never 0.0.0.0 or "localhost": only this machine may answer.
                server = await asyncio.start_server(handle, "127.0.0.1", port)
            except OSError:
                continue
            callback = cls(server, port)
            holder.append(callback)
            return callback
        raise SignInError(ports_busy())

    def expect_state(self, state: str) -> None:
        self._expected = state

    async def result(self) -> CallbackResult:
        return await asyncio.shield(self._result)

    async def close(self) -> None:
        self._server.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._server.wait_closed(), 1)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            async with asyncio.timeout(_READ_TIMEOUT_S):
                head = await reader.readuntil(b"\r\n\r\n")
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return
        if len(head) > _MAX_REQUEST_BYTES:
            await _reply(writer, 431, "stale")
            return
        method, target = _request_line(head)
        parts = urlsplit(target)
        if method != "GET" or parts.path != CALLBACK_PATH:
            await _reply(writer, 404, "stale")
            return
        result = _result_from_query(parts.query)
        if (
            self._expected is None
            or result.state != self._expected
            or self._result.done()
        ):
            await _reply(writer, 400, "stale")
            return
        await _reply(writer, 200, "declined" if result.error else "ok")
        self._result.set_result(result)
        self._server.close()


def _request_line(head: bytes) -> tuple[str, str]:
    first = head.split(b"\r\n", 1)[0].decode("latin-1")
    pieces = first.split(" ")
    return (pieces[0], pieces[1]) if len(pieces) >= 2 else ("", "")


async def _reply(writer: asyncio.StreamWriter, status: int, page: str) -> None:
    body = (
        "<!doctype html><meta charset=utf-8><title>Aldwin</title>"
        f"<p style='font:16px system-ui;margin:3em'>{_PAGES[page]}</p>"
    ).encode()
    reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 431: "Too Large"}[status]
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    with contextlib.suppress(Exception):
        await writer.drain()
    writer.close()
