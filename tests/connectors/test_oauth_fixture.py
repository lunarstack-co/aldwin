"""The OAuth fixture server behaves like a real authorization server."""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

pytestmark = pytest.mark.loopback

REDIRECT = "http://127.0.0.1:47823/callback"


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


async def _code(http: httpx2.AsyncClient, fixture: Any) -> tuple[str, str]:
    reg = await http.post(
        f"{fixture.base_url}/register", json={"redirect_uris": [REDIRECT]}
    )
    assert reg.status_code == 201
    client_id = reg.json()["client_id"]
    verifier = "v" * 50
    auth = await http.get(
        f"{fixture.base_url}/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "state": "s1",
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        },
    )
    assert auth.status_code == 302
    query = parse_qs(urlsplit(auth.headers["location"]).query)
    assert query["state"] == ["s1"]
    assert query["iss"] == [fixture.issuer]
    return client_id, query["code"][0]


async def test_register_authorize_and_exchange(oauth_server: Any) -> None:
    async with httpx2.AsyncClient(follow_redirects=False) as http:
        client_id, code = await _code(http, oauth_server)
        token = await http.post(
            f"{oauth_server.base_url}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": REDIRECT,
                "code_verifier": "v" * 50,
            },
        )
    assert token.status_code == 200
    assert oauth_server.bearer_ok(f"Bearer {token.json()['access_token']}")
    assert oauth_server.requests["register"] == 1
    assert oauth_server.requests["token:authorization_code"] == 1


async def test_wrong_verifier_is_refused(oauth_server: Any) -> None:
    async with httpx2.AsyncClient(follow_redirects=False) as http:
        client_id, code = await _code(http, oauth_server)
        token = await http.post(
            f"{oauth_server.base_url}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "redirect_uri": REDIRECT,
                "code_verifier": "w" * 50,
            },
        )
    assert token.status_code == 400


async def test_mcp_needs_a_bearer_and_points_at_metadata(oauth_server: Any) -> None:
    async with httpx2.AsyncClient() as http:
        response = await http.post(oauth_server.mcp_url, json={})
    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]
    assert oauth_server.requests["mcp"] == 1
