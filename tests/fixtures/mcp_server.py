"""A real MCP server for connector tests, in-process or over HTTP on loopback.

Five tools, one read-only: `read_thing` (read-only hint), `write_thing`, a
70-character name (`LONG_TOOL`), `echo_nullable` (a `["string","null"]`
argument) and `fail_thing` (a tool error). `FixtureControls` makes it misbehave.
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
PNG_BLOCK = ImageContent(
    type="image",
    data=base64.b64encode(PNG_BYTES).decode("ascii"),
    mime_type="image/png",
)

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Scope]]
Send = Callable[[Scope], Awaitable[None]]


class FixtureServer(MCPServer):
    """Records each session that lists tools, so `swap_tools` can notify it."""

    def __init__(self, controls: FixtureControls) -> None:
        super().__init__("fixture")
        self.controls = controls
        self.sessions: list[Any] = []

    async def _handle_list_tools(self, ctx: Any, params: Any) -> ListToolsResult:
        if ctx.session not in self.sessions:
            self.sessions.append(ctx.session)
        if self.controls.connect_delay:
            await asyncio.sleep(self.controls.connect_delay)
        return await super()._handle_list_tools(ctx, params)


@dataclass
class FixtureControls:
    """Switches a test flips to make the fixture misbehave."""

    server: FixtureServer = field(init=False)
    bearer: str | None = None
    requests: list[dict[str, str]] = field(default_factory=list)
    connect_delay: float = 0.0
    expire_next: bool = False
    _contents: dict[str, list[Any]] = field(default_factory=dict)
    _delays: dict[str, float] = field(default_factory=dict)
    _hung: set[str] = field(default_factory=set)

    def require_bearer(self, token: str) -> None:
        """Answer 401 to every HTTP request without `Authorization: Bearer <token>`."""
        self.bearer = token

    def delay_connect(self, seconds: float) -> None:
        """Delay every tool listing, which connecting waits on in both eras."""
        self.connect_delay = seconds

    def content(self, name: str, blocks: list[Any]) -> None:
        """Make tool `name` return exactly these content blocks."""
        self._contents[name] = blocks

    def slow_tool(self, name: str, seconds: float) -> None:
        self._delays[name] = seconds

    def hang_tool(self, name: str) -> None:
        """Make tool `name` never answer."""
        self._hung.add(name)

    def expire_session(self) -> None:
        """Answer the next HTTP session request with 404 `Session terminated`."""
        self.expire_next = True

    async def swap_tools(self) -> None:
        """Add `new_thing`, remove `write_thing`, and announce it in both eras."""

        async def new_thing() -> Any:
            return await self.run("new_thing", "new ok")

        self.server.add_tool(new_thing, structured_output=False)
        self.server.remove_tool("write_thing")
        for session in self.server.sessions:
            try:
                await session.send_tool_list_changed()
            except Exception:
                pass  # 2026-era sessions have no push channel; the bus covers them.
        await self.server._subscriptions.publish(ToolsListChanged())

    async def run(self, name: str, default: Any) -> Any:
        """Apply this tool's switches, then return its content or `default`."""
        if name in self._hung:
            await asyncio.Event().wait()
        if name in self._delays:
            await asyncio.sleep(self._delays[name])
        return self._contents.get(name, default)


def build_fixture_server() -> FixtureControls:
    controls = FixtureControls()
    server = FixtureServer(controls)
    controls.server = server

    async def read_thing() -> Any:
        """Read something harmless."""
        return await controls.run("read_thing", "read ok")

    async def write_thing(value: str) -> Any:
        """Change something."""
        return await controls.run("write_thing", f"wrote {value}")

    async def long_tool() -> Any:
        return await controls.run(LONG_TOOL, "long ok")

    async def echo_nullable(value: str | None) -> Any:
        """Echo a value that may be null."""
        return await controls.run("echo_nullable", repr(value))

    async def fail_thing() -> Any:
        """Always fails."""
        await controls.run("fail_thing", None)
        raise ToolError("bad input")

    server.add_tool(
        read_thing,
        annotations=ToolAnnotations(read_only_hint=True),
        structured_output=False,
    )
    server.add_tool(write_thing, structured_output=False)
    server.add_tool(
        long_tool,
        name=LONG_TOOL,
        description="A very long name.",
        structured_output=False,
    )
    server.add_tool(echo_nullable, structured_output=False)
    server.add_tool(fail_thing, structured_output=False)

    stored = server._tool_manager.get_tool("echo_nullable")
    assert stored is not None
    stored.parameters = NULLABLE_SCHEMA
    return controls


def _wrap(app: Any, controls: FixtureControls) -> Any:
    """Add the `/401` and `/html` routes, header recording and the switches."""

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
        if controls.expire_next and "mcp-session-id" in headers:
            controls.expire_next = False
            await _respond(send, 404, b"Session terminated", b"text/plain")
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
