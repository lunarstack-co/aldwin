"""A real MCP server for connector tests, in-process or over HTTP on loopback.

Tools: `read_thing` (read-only hint), `write_thing`, a 70-character name
(`LONG_TOOL`), `echo_nullable` (a `["string","null"]` argument), `fail_thing`
(a tool error) and `picture` (an image block).
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.subscriptions import ToolsListChanged
from mcp_types import ImageContent, ListToolsResult, ToolAnnotations

LONG_TOOL = "t" * 70
NULLABLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {"type": ["string", "null"]}},
    "required": ["value"],
}
# A 1x1 transparent PNG.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Scope]]
Send = Callable[[Scope], Awaitable[None]]


class FixtureServer(MCPServer):
    """Records each session that lists tools, so `swap_tools` can notify it."""

    def __init__(self) -> None:
        super().__init__("fixture")
        self.sessions: list[Any] = []

    async def _handle_list_tools(self, ctx: Any, params: Any) -> ListToolsResult:
        if ctx.session not in self.sessions:
            self.sessions.append(ctx.session)
        return await super()._handle_list_tools(ctx, params)


@dataclass
class FixtureControls:
    """Switches a test flips to make the fixture misbehave."""

    server: FixtureServer
    bearer: str | None = None
    requests: list[dict[str, str]] = field(default_factory=list)

    def require_bearer(self, token: str) -> None:
        """Answer 401 to every HTTP request without `Authorization: Bearer <token>`."""
        self.bearer = token

    async def swap_tools(self) -> None:
        """Add `new_thing`, remove `write_thing`, and announce it in both eras."""

        def new_thing() -> str:
            return "new ok"

        self.server.add_tool(new_thing)
        self.server.remove_tool("write_thing")
        for session in self.server.sessions:
            try:
                await session.send_tool_list_changed()
            except Exception:
                pass  # 2026-era sessions have no push channel; the bus covers them.
        await self.server._subscriptions.publish(ToolsListChanged())


def build_fixture_server() -> FixtureControls:
    server = FixtureServer()

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def read_thing() -> str:
        """Read something harmless."""
        return "read ok"

    @server.tool()
    def write_thing(value: str) -> str:
        """Change something."""
        return f"wrote {value}"

    def long_tool() -> str:
        return "long ok"

    server.add_tool(long_tool, name=LONG_TOOL, description="A very long name.")

    @server.tool()
    def echo_nullable(value: str | None) -> str:
        """Echo a value that may be null."""
        return repr(value)

    stored = server._tool_manager.get_tool("echo_nullable")
    assert stored is not None
    stored.parameters = NULLABLE_SCHEMA

    @server.tool()
    def fail_thing() -> str:
        """Always fails."""
        raise ToolError("bad input")

    @server.tool()
    def picture() -> list[ImageContent]:
        """Return a tiny image."""
        data = base64.b64encode(PNG_BYTES).decode("ascii")
        return [ImageContent(type="image", data=data, mime_type="image/png")]

    return FixtureControls(server=server)


def _wrap(app: Any, controls: FixtureControls) -> Any:
    """Add the `/401` and `/html` routes, header recording and the bearer check."""

    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        controls.requests.append(headers)
        path = scope["path"]
        if path == "/401" or (
            controls.bearer is not None
            and headers.get("authorization") != f"Bearer {controls.bearer}"
        ):
            await _respond(send, 401, b'{"error":"unauthorized"}', b"application/json")
            return
        if path == "/html":
            await _respond(send, 200, b"<html>hi</html>", b"text/html; charset=utf-8")
            return
        await app(scope, receive, send)

    return asgi


async def _respond(send: Send, status: int, body: bytes, content_type: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type)],
        }
    )
    await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def serve_http(
    controls: FixtureControls, port: int | None = None
) -> AsyncIterator[str]:
    """Serve the fixture on `127.0.0.1` and yield its base URL (`.../mcp` is MCP).

    Pass a previous port to restart on the same address.
    """
    app = _wrap(controls.server.streamable_http_app(), controls)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port or 0,
        log_level="warning",
        lifespan="on",
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    bound = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{bound}"
    finally:
        server.should_exit = True
        await task
