"""Shared helpers for tests that need a connector already signed in."""

from __future__ import annotations

import time
from typing import Any

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.credentials import CredentialStore

REDIRECT = "http://127.0.0.1:47823/callback"


def oauth_config(fixture: Any, name: str = "fixture") -> ConnectorConfig:
    return ConnectorConfig(name=name, url=fixture.mcp_url, auth="oauth")


def seed_signed_in(
    fixture: Any,
    store: CredentialStore,
    name: str = "fixture",
    *,
    expires_in: float = 3600,
) -> dict[str, Any]:
    """Write a credentials file as if `name` had signed in to `fixture`."""
    _status, client = fixture.register({"redirect_uris": [REDIRECT]})
    tokens = fixture._issue(client["client_id"])
    record = {
        "v": 1,
        "kind": "oauth",
        "issuer": fixture.issuer,
        "client_info": client,
        "tokens": tokens,
        "expires_at": time.time() + expires_in,
        "oauth_metadata": fixture.authorization_server(),
    }
    store.set(name, record)
    return record
