"""Connector secrets, kept out of `connectors.yaml`.

Files live at `~/.aldwin/credentials/mcp/<name>.json` (folder 0700, file 0600).
Stored shape: `{"v": 1, "kind": "bearer", "token": "<secret>"}`.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from aldwin.core.connectors.config import (
    ConnectorConfig,
    credentials_dir,
    is_valid_name,
)
from aldwin.core.home import DIR_MODE, atomic_write

logger = logging.getLogger("aldwin.core.connectors.credentials")

CREDENTIAL_VERSION = 1


class CredentialStore(Protocol):
    """Where connector secrets are kept. The OS keychain can implement this later."""

    @property
    def directory(self) -> Path: ...
    def get(self, name: str) -> dict[str, Any] | None: ...
    def set(self, name: str, data: dict[str, Any]) -> None: ...
    def delete(self, name: str) -> None: ...


class FileCredentialStore:
    """One JSON file per connector, written atomically under a file lock."""

    def __init__(self, home: Path) -> None:
        self._dir = credentials_dir(home)

    @property
    def directory(self) -> Path:
        """The folder holding the files (created 0700 on first write)."""
        self._ensure_dir()
        return self._dir

    def get(self, name: str) -> dict[str, Any] | None:
        path = self._path(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            # Unreadable means "no credential": the connector asks for a token again.
            logger.warning("ignoring unreadable credential file for %s", name)
            return None
        return data if isinstance(data, dict) else None

    def set(self, name: str, data: dict[str, Any]) -> None:
        path = self._path(name)
        with self._locked(name):
            atomic_write(path, json.dumps(data, sort_keys=True))

    def delete(self, name: str) -> None:
        path = self._path(name)
        with self._locked(name):
            path.unlink(missing_ok=True)

    def _path(self, name: str) -> Path:
        # The name becomes a file name, so it must never contain a path.
        if not is_valid_name(name):
            raise ValueError(f"invalid connector name: {name!r}")
        return self._dir / f"{name}.json"

    def _ensure_dir(self) -> None:
        for folder in (self._dir.parent, self._dir):
            folder.mkdir(exist_ok=True, mode=DIR_MODE)
            os.chmod(folder, DIR_MODE)

    @contextmanager
    def _locked(self, name: str) -> Iterator[None]:
        self._ensure_dir()
        fd = os.open(self._dir / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def bearer_credential(token: str) -> dict[str, Any]:
    """The stored shape for a bearer token."""
    return {"v": CREDENTIAL_VERSION, "kind": "bearer", "token": token}


def resolve_bearer_token(
    config: ConnectorConfig, store: CredentialStore, environ: Mapping[str, str]
) -> str | None:
    """The token to send: `token_env` wins when that variable is set, else the store."""
    if config.auth != "bearer":
        return None
    if config.token_env and environ.get(config.token_env):
        return environ[config.token_env]
    data = store.get(config.name)
    if data and data.get("kind") == "bearer" and isinstance(data.get("token"), str):
        return str(data["token"])
    return None


def credential_identity(data: dict[str, Any] | None) -> tuple[Any, ...] | None:
    """What a connector's credential *is*, ignoring rewrites that change nothing.

    A new bearer token changes the identity; rewriting the same token does not.
    """
    if data is None:
        return None
    kind = data.get("kind")
    if kind == "bearer":
        token = str(data.get("token", ""))
        return ("bearer", hashlib.sha256(token.encode("utf-8")).hexdigest())
    if kind == "oauth":
        # A refresh rewrites tokens only; who signed in (issuer, client) is what counts.
        client_info = data.get("client_info") or {}
        return ("oauth", data.get("issuer"), client_info.get("client_id"))
    return (str(kind),)
