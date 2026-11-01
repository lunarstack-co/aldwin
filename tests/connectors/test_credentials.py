"""Connector credential store (`FR-2`)."""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path

import pytest

from aldwin.core.connectors.config import ConnectorConfig, credentials_dir
from aldwin.core.connectors.credentials import (
    FileCredentialStore,
    bearer_credential,
    credential_identity,
    resolve_bearer_token,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _bearer(name: str = "github", token_env: str | None = None) -> ConnectorConfig:
    return ConnectorConfig(
        name=name,
        url="https://api.githubcopilot.com/mcp/",
        auth="bearer",
        token_env=token_env,
    )


def test_set_get_delete_and_modes(aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    assert store.get("github") is None

    store.set("github", bearer_credential("tok-1"))
    assert store.get("github") == {"v": 1, "kind": "bearer", "token": "tok-1"}

    folder = credentials_dir(aldwin_home)
    assert _mode(folder) == 0o700
    assert _mode(folder.parent) == 0o700
    assert _mode(folder / "github.json") == 0o600

    store.delete("github")
    assert store.get("github") is None
    store.delete("github")


def test_names_cannot_escape_the_folder(aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    for bad in ("../config", "a/b", "UPPER", ""):
        with pytest.raises(ValueError):
            store.set(bad, bearer_credential("x"))
        with pytest.raises(ValueError):
            store.get(bad)


def test_unreadable_file_reads_as_missing(aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    store.set("github", bearer_credential("tok"))
    (credentials_dir(aldwin_home) / "github.json").write_text("{nope", "utf-8")
    assert store.get("github") is None


def test_token_env_wins_then_store(aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    store.set("github", bearer_credential("stored"))
    config = _bearer(token_env="GITHUB_TOKEN")

    assert resolve_bearer_token(config, store, {"GITHUB_TOKEN": "env"}) == "env"
    assert resolve_bearer_token(config, store, {"GITHUB_TOKEN": ""}) == "stored"
    assert resolve_bearer_token(config, store, {}) == "stored"
    assert resolve_bearer_token(_bearer("other"), store, {}) is None

    no_auth = ConnectorConfig(name="github", url="https://x.example/mcp")
    assert resolve_bearer_token(no_auth, store, {"GITHUB_TOKEN": "env"}) is None


def test_credential_identity_tracks_the_token_only() -> None:
    a = credential_identity(bearer_credential("one"))
    assert a == credential_identity({"token": "one", "kind": "bearer", "v": 1})
    assert a != credential_identity(bearer_credential("two"))
    assert credential_identity(None) is None
    assert credential_identity({"kind": "other"}) == ("other",)
    assert "one" not in repr(a)


def test_concurrent_writers_leave_valid_json(aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    tokens = [f"token-{i}" * 50 for i in range(8)]

    def write(token: str) -> None:
        for _ in range(20):
            store.set("github", bearer_credential(token))

    threads = [threading.Thread(target=write, args=(t,)) for t in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    path = credentials_dir(aldwin_home) / "github.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["token"] in tokens
