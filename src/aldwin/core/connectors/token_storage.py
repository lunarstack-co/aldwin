"""OAuth tokens on top of the connector credential store.

The file shape is `{"v": 1, "kind": "oauth", "issuer", "client_info", "tokens",
"expires_at", "oauth_metadata"}`. `expires_at` and the server metadata are kept
because the SDK forgets both on restart: without them a stored token is never
refreshed early, and a refresh goes to a guessed `/token` endpoint.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

from aldwin.core.connectors.credentials import CredentialStore
from aldwin.core.connectors.oauth_messages import REFRESH_FENCE_TIMEOUT_S
from aldwin.core.home import FILE_MODE

_POLL_S = 0.05


class CredentialTokenStorage:
    """The SDK `TokenStorage` for one connector.

    Staged (interactive sign-in): everything stays in memory until `commit()`,
    so a sign-in that fails leaves no file. Otherwise every write goes to disk.
    """

    def __init__(
        self,
        store: CredentialStore,
        name: str,
        *,
        staged: bool = False,
        client_info: OAuthClientInformationFull | None = None,
    ) -> None:
        self._store = store
        self._name = name
        self._staged = staged
        self._tokens: OAuthToken | None = None
        self._client_info = client_info
        self._expires_at: float | None = None

    async def get_tokens(self) -> OAuthToken | None:
        if self._staged:
            return self._tokens
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens
        self._expires_at = (
            time.time() + tokens.expires_in if tokens.expires_in is not None else None
        )
        if not self._staged:
            self._write(
                tokens=tokens.model_dump(mode="json", exclude_none=True),
                expires_at=self._expires_at,
            )

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        if self._staged:
            return self._client_info
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client_info = client_info
        if not self._staged:
            self._write(
                client_info=client_info.model_dump(mode="json", exclude_none=True)
            )

    def load_extras(self) -> tuple[float | None, OAuthMetadata | None, str | None]:
        """`(expires_at, server metadata, issuer)` saved with the tokens."""
        data = self._read()
        raw_meta = data.get("oauth_metadata")
        metadata = OAuthMetadata.model_validate(raw_meta) if raw_meta else None
        expires_at = data.get("expires_at")
        return (
            float(expires_at) if isinstance(expires_at, int | float) else None,
            metadata,
            data.get("issuer"),
        )

    def commit(self, *, issuer: str, metadata: OAuthMetadata | None) -> None:
        """Write a staged sign-in in one go, replacing any older file."""
        if self._tokens is None or self._client_info is None:
            raise ValueError("nothing to commit: sign-in did not finish")
        record: dict[str, Any] = {
            "v": 1,
            "kind": "oauth",
            "issuer": issuer,
            "client_info": self._client_info.model_dump(mode="json", exclude_none=True),
            "tokens": self._tokens.model_dump(mode="json", exclude_none=True),
            "expires_at": self._expires_at,
        }
        if metadata is not None:
            record["oauth_metadata"] = metadata.model_dump(
                mode="json", exclude_none=True
            )
        self._store.set(self._name, record)
        self._staged = False

    def _read(self) -> dict[str, Any]:
        data = self._store.get(self._name)
        return data if data and data.get("kind") == "oauth" else {}

    def _write(self, **fields: Any) -> None:
        record: dict[str, Any] = {"v": 1, "kind": "oauth", **self._read(), **fields}
        self._store.set(self._name, record)


@asynccontextmanager
async def refresh_fence(
    directory: Path, name: str, *, timeout: float = REFRESH_FENCE_TIMEOUT_S
) -> AsyncIterator[None]:
    """Hold `<name>.refresh.lock` across a token refresh, across processes.

    Polled without blocking so the event loop keeps running. Raises
    `TimeoutError` rather than refreshing without the lock.
    """
    fd = os.open(directory / f"{name}.refresh.lock", os.O_RDWR | os.O_CREAT, FILE_MODE)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"{name} refresh lock is held elsewhere"
                    ) from None
                await asyncio.sleep(_POLL_S)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
