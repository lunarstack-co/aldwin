"""OAuth settings in `connectors.yaml` and the OAuth credential identity."""

from __future__ import annotations

from pathlib import Path

from aldwin.core.connectors.config import (
    ConnectorConfig,
    OAuthSettings,
    connectors_fingerprint,
    connectors_path,
    credentials_dir,
    load_connectors,
    save_connectors,
)
from aldwin.core.connectors.credentials import credential_identity
from aldwin.core.connectors.oauth_messages import DEFAULT_CLIENT_METADATA_URL


def test_oauth_entry_round_trips(aldwin_home: Path) -> None:
    linear = ConnectorConfig(
        name="linear",
        url="https://mcp.linear.app/mcp",
        auth="oauth",
        oauth=OAuthSettings(client_id="pre-registered"),
    )
    save_connectors(aldwin_home, {"linear": linear})
    load = load_connectors(aldwin_home)
    assert load.entries["linear"] == linear
    assert load.oauth_client_metadata_url == DEFAULT_CLIENT_METADATA_URL


def test_custom_metadata_url_is_read_and_kept(aldwin_home: Path) -> None:
    connectors_path(aldwin_home).write_text(
        "oauth_client_metadata_url: https://me.example/client.json\nconnectors: {}\n",
        encoding="utf-8",
    )
    assert (
        load_connectors(aldwin_home).oauth_client_metadata_url
        == "https://me.example/client.json"
    )
    save_connectors(
        aldwin_home, {"x": ConnectorConfig(name="x", url="https://x.example/mcp")}
    )
    assert (
        load_connectors(aldwin_home).oauth_client_metadata_url
        == "https://me.example/client.json"
    )


def test_invalid_metadata_url_turns_connectors_off(aldwin_home: Path) -> None:
    connectors_path(aldwin_home).write_text(
        "oauth_client_metadata_url: http://me.example/client.json\n", encoding="utf-8"
    )
    load = load_connectors(aldwin_home)
    assert load.entries == {}
    assert (
        load.error == "connectors.yaml: oauth_client_metadata_url must be an https URL"
    )


def test_refresh_lock_does_not_change_the_fingerprint(aldwin_home: Path) -> None:
    creds = credentials_dir(aldwin_home)
    creds.mkdir(parents=True)
    before = connectors_fingerprint(aldwin_home)
    (creds / "linear.refresh.lock").write_text("", encoding="utf-8")
    assert connectors_fingerprint(aldwin_home) == before


def test_identity_ignores_refreshes_but_not_a_new_sign_in() -> None:
    base = {
        "v": 1,
        "kind": "oauth",
        "issuer": "https://auth.example",
        "client_info": {"client_id": "c1"},
        "tokens": {"access_token": "a1"},
        "expires_at": 1.0,
    }
    refreshed = {**base, "tokens": {"access_token": "a2"}, "expires_at": 2.0}
    assert credential_identity(base) == credential_identity(refreshed)
    assert credential_identity(base) == ("oauth", "https://auth.example", "c1")
    assert credential_identity(base) != credential_identity(
        {**base, "issuer": "https://other.example"}
    )
    assert credential_identity(base) != credential_identity(
        {**base, "client_info": {"client_id": "c2"}}
    )
