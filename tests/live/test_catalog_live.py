"""Opt-in checks that each catalogue app still signs in the way Aldwin expects
(`uv run pytest -m live tests/live/test_catalog_live.py`).
"""

from __future__ import annotations

import httpx2
import pytest

from aldwin.core.connectors.catalog import CatalogEntry, load_catalog
from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.oauth import choose_client_identity, discover, require_s256
from aldwin.core.connectors.oauth_messages import DEFAULT_CLIENT_METADATA_URL

pytestmark = pytest.mark.live


def _config(entry: CatalogEntry) -> ConnectorConfig:
    return ConnectorConfig(
        name=entry.name, url=entry.url, auth=entry.auth, oauth=entry.oauth
    )


@pytest.mark.parametrize(
    "entry", [e for e in load_catalog() if e.auth == "oauth"], ids=lambda e: e.name
)
async def test_browser_sign_in_apps_are_ready(entry: CatalogEntry) -> None:
    """Discovery works, PKCE S256 is offered, and Aldwin has a client identity."""
    async with httpx2.AsyncClient() as http:
        found = await discover(_config(entry), client=http)
    assert found is not None, f"{entry.name} no longer asks for sign-in"
    require_s256(entry.title, found.asm)
    identity = choose_client_identity(
        _config(entry), found.asm, DEFAULT_CLIENT_METADATA_URL
    )
    assert identity.kind in {"preregistered", "cimd", "dcr"}


@pytest.mark.parametrize(
    "entry", [e for e in load_catalog() if e.auth != "oauth"], ids=lambda e: e.name
)
async def test_token_and_open_apps_answer(entry: CatalogEntry) -> None:
    """A token app refuses an anonymous call; an open app needs no sign-in."""
    async with httpx2.AsyncClient() as http:
        if entry.auth == "none":
            assert await discover(_config(entry), client=http) is None
            return
        response = await http.post(
            entry.url,
            json={"jsonrpc": "2.0", "id": 0, "method": "ping"},
            headers={"accept": "application/json, text/event-stream"},
        )
    assert response.status_code == 401
