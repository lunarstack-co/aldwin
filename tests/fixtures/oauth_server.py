"""A local OAuth authorization server in front of the fixture MCP server.

Supports PKCE (S256), dynamic client registration, CIMD client IDs, refresh
with rotation and revocation. Each switch on `OAuthFixtureConfig` turns one
behaviour off or on, so tests can reproduce real servers' quirks.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

import uvicorn

from fixtures.mcp_server import FixtureControls, build_fixture_server

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Scope]]
Send = Callable[[Scope], Awaitable[None]]

CIMD_DOC = json.loads((Path(__file__).parent / "oauth_client.json").read_text("utf-8"))


@dataclass
class OAuthFixtureConfig:
    s256: bool = True
    cimd: bool = False
    dcr: bool = True
    send_iss: bool = True
    access_ttl: int = 3600
    rotate_refresh: bool = True
    prm_in_header: bool = True
    token_path: str = "/token"
    register_status: int = 201


@dataclass
class _Code:
    client_id: str
    redirect_uri: str
    challenge: str


@dataclass
class OAuthFixture:
    """The server's state, counters and switches."""

    config: OAuthFixtureConfig = field(default_factory=OAuthFixtureConfig)
    mcp: FixtureControls = field(default_factory=build_fixture_server)
    base_url: str = ""
    requests: Counter[str] = field(default_factory=Counter)
    clients: dict[str, list[str]] = field(default_factory=dict)
    codes: dict[str, _Code] = field(default_factory=dict)
    access_tokens: dict[str, float] = field(default_factory=dict)
    refresh_tokens: dict[str, str] = field(default_factory=dict)
    last_authorize_params: dict[str, str] = field(default_factory=dict)
    last_token_params: dict[str, str] = field(default_factory=dict)
    last_register_body: dict[str, Any] = field(default_factory=dict)
    issued_refresh_tokens: list[str] = field(default_factory=list)
    issued_codes: list[str] = field(default_factory=list)

    @property
    def issuer(self) -> str:
        return self.base_url

    @property
    def mcp_url(self) -> str:
        return f"{self.base_url}/mcp"

    def expire_access_tokens(self) -> None:
        for token in self.access_tokens:
            self.access_tokens[token] = 0.0

    def revoke_access_tokens(self) -> None:
        self.access_tokens.clear()

    def revoke_refresh_tokens(self) -> None:
        self.refresh_tokens.clear()

    def revoke_all(self) -> None:
        self.revoke_access_tokens()
        self.revoke_refresh_tokens()

    # --- metadata -----------------------------------------------------------

    def protected_resource(self) -> dict[str, Any]:
        return {"resource": self.mcp_url, "authorization_servers": [self.issuer]}

    def authorization_server(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.base_url}/authorize",
            "token_endpoint": f"{self.base_url}{self.config.token_path}",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
        if self.config.s256:
            meta["code_challenge_methods_supported"] = ["S256"]
        if self.config.dcr:
            meta["registration_endpoint"] = f"{self.base_url}/register"
        if self.config.cimd:
            meta["client_id_metadata_document_supported"] = True
        return meta

    # --- handlers -----------------------------------------------------------

    def authorize(self, params: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        self.last_authorize_params = params
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        allowed = (
            CIMD_DOC["redirect_uris"]
            if self.config.cimd and client_id == CIMD_DOC["client_id"]
            else self.clients.get(client_id)
        )
        if (
            allowed is None
            or redirect_uri not in allowed
            or params.get("code_challenge_method") != "S256"
            or not params.get("state")
            or not params.get("code_challenge")
        ):
            return 400, {}, b'{"error":"invalid_request"}'
        code = f"code-{secrets.token_hex(8)}"
        self.codes[code] = _Code(client_id, redirect_uri, params["code_challenge"])
        self.issued_codes.append(code)
        query = {"code": code, "state": params["state"]}
        if self.config.send_iss:
            query["iss"] = self.issuer
        return 302, {"location": f"{redirect_uri}?{urlencode(query)}"}, b""

    def token(self, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.last_token_params = form
        grant = form.get("grant_type", "")
        self.requests[f"token:{grant}"] += 1
        if grant == "authorization_code":
            code = self.codes.pop(form.get("code", ""), None)
            verifier = form.get("code_verifier", "")
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            if (
                code is None
                or code.client_id != form.get("client_id")
                or code.redirect_uri != form.get("redirect_uri")
                or code.challenge != challenge
            ):
                return 400, {"error": "invalid_grant"}
            return 200, self._issue(code.client_id)
        if grant == "refresh_token":
            client_id = self.refresh_tokens.get(form.get("refresh_token", ""))
            if client_id is None or client_id != form.get("client_id"):
                return 400, {"error": "invalid_grant"}
            if self.config.rotate_refresh:
                del self.refresh_tokens[form["refresh_token"]]
                return 200, self._issue(client_id)
            return 200, self._issue(client_id, refresh=False)
        return 400, {"error": "unsupported_grant_type"}

    def register(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.last_register_body = body
        if self.config.register_status != 201:
            return self.config.register_status, {"error": "invalid_client_metadata"}
        client_id = f"client-{secrets.token_hex(6)}"
        self.clients[client_id] = list(body.get("redirect_uris", []))
        return 201, {
            **body,
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "token_endpoint_auth_method": "none",
        }

    def _issue(self, client_id: str, *, refresh: bool = True) -> dict[str, Any]:
        access = f"access-{secrets.token_hex(8)}"
        self.access_tokens[access] = time.time() + self.config.access_ttl
        body: dict[str, Any] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": self.config.access_ttl,
        }
        if refresh:
            new_refresh = f"refresh-{secrets.token_hex(8)}"
            self.refresh_tokens[new_refresh] = client_id
            self.issued_refresh_tokens.append(new_refresh)
            body["refresh_token"] = new_refresh
        return body

    def bearer_ok(self, header: str | None) -> bool:
        if not header or not header.startswith("Bearer "):
            return False
        expiry = self.access_tokens.get(header.removeprefix("Bearer "))
        return expiry is not None and expiry > time.time()


def build_app(fixture: OAuthFixture) -> Any:
    mcp_app = fixture.mcp.server.streamable_http_app()

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await mcp_app(
                scope, receive, send
            )  # lifespan starts the MCP session manager
            return
        path: str = scope["path"]
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}

        if path == "/mcp":
            fixture.requests["mcp"] += 1
            if fixture.bearer_ok(headers.get("authorization")):
                await mcp_app(scope, receive, send)
                return
            challenge = "Bearer"
            if fixture.config.prm_in_header:
                prm = f"{fixture.base_url}/.well-known/oauth-protected-resource/mcp"
                challenge = f'Bearer resource_metadata="{prm}"'
            await _json(
                send, 401, {"error": "invalid_token"}, {"www-authenticate": challenge}
            )
            return
        if path.startswith("/.well-known/oauth-protected-resource"):
            fixture.requests["well_known"] += 1
            await _json(send, 200, fixture.protected_resource())
            return
        if path.startswith("/.well-known/oauth-authorization-server"):
            fixture.requests["well_known"] += 1
            await _json(send, 200, fixture.authorization_server())
            return
        if path == "/authorize":
            fixture.requests["authorize"] += 1
            params = {
                k: v[0] for k, v in parse_qs(scope["query_string"].decode()).items()
            }
            status, extra, body = fixture.authorize(params)
            await _respond(send, status, body, "application/json", extra)
            return
        if path == fixture.config.token_path and scope["method"] == "POST":
            fixture.requests["token"] += 1
            form = {
                k: v[0] for k, v in parse_qs((await _body(receive)).decode()).items()
            }
            status, payload = fixture.token(form)
            await _json(send, status, payload)
            return
        if path == "/register" and scope["method"] == "POST" and fixture.config.dcr:
            fixture.requests["register"] += 1
            status, payload = fixture.register(
                json.loads(await _body(receive) or b"{}")
            )
            await _json(send, status, payload)
            return
        if path == "/revoke":
            fixture.requests["revoke"] += 1
            await _json(send, 200, {})
            return
        fixture.requests[f"404:{path}"] += 1
        await _json(send, 404, {"error": "not_found"})

    return app


async def _body(receive: Receive) -> bytes:
    chunks = []
    while True:
        message = await receive()
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            return b"".join(chunks)


async def _json(
    send: Send, status: int, payload: Any, headers: dict[str, str] | None = None
) -> None:
    await _respond(
        send, status, json.dumps(payload).encode(), "application/json", headers
    )


async def _respond(
    send: Send,
    status: int,
    body: bytes,
    content_type: str,
    headers: dict[str, str] | None = None,
) -> None:
    raw = [(b"content-type", content_type.encode())]
    raw += [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def serve_oauth(fixture: OAuthFixture) -> AsyncIterator[OAuthFixture]:
    """Serve on a free `127.0.0.1` port; `fixture.base_url` is set while it runs."""
    config = uvicorn.Config(
        build_app(fixture),
        host="127.0.0.1",
        port=0,
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
    port = server.servers[0].sockets[0].getsockname()[1]
    fixture.base_url = f"http://127.0.0.1:{port}"
    try:
        yield fixture
    finally:
        server.should_exit = True
        await task
