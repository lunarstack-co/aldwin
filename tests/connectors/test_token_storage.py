"""OAuth token storage and the cross-process refresh fence."""

from __future__ import annotations

import asyncio
import fcntl
import multiprocessing
import os
import stat
import time
from pathlib import Path

import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

from aldwin.core.connectors.config import credentials_dir
from aldwin.core.connectors.credentials import (
    FileCredentialStore,
    bearer_credential,
)
from aldwin.core.connectors.token_storage import CredentialTokenStorage, refresh_fence

TOKEN = OAuthToken(
    access_token="a1", token_type="Bearer", expires_in=600, refresh_token="r1"
)
CLIENT = OAuthClientInformationFull(
    client_id="c1",
    redirect_uris=["http://127.0.0.1:47823/callback"],
    token_endpoint_auth_method="none",
)
META = OAuthMetadata(
    issuer="https://auth.example",
    authorization_endpoint="https://auth.example/authorize",
    token_endpoint="https://auth.example/token",
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


async def test_write_through_round_trip_and_modes(aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    storage = CredentialTokenStorage(store, "linear")
    await storage.set_client_info(CLIENT)
    before = time.time()
    await storage.set_tokens(TOKEN)

    again = CredentialTokenStorage(store, "linear")
    assert await again.get_tokens() == TOKEN
    assert await again.get_client_info() == CLIENT
    data = store.get("linear")
    assert data is not None
    assert (data["v"], data["kind"]) == (1, "oauth")
    assert before + 599 <= data["expires_at"] <= time.time() + 600
    path = credentials_dir(aldwin_home) / "linear.json"
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700


async def test_staged_writes_nothing_until_commit(aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    staged = CredentialTokenStorage(store, "linear", staged=True)
    assert await staged.get_tokens() is None
    await staged.set_client_info(CLIENT)
    await staged.set_tokens(TOKEN)
    assert store.get("linear") is None

    staged.commit(issuer="https://auth.example", metadata=META)
    expires_at, metadata, issuer = CredentialTokenStorage(store, "linear").load_extras()
    assert issuer == "https://auth.example"
    assert metadata == META
    assert expires_at is not None


async def test_commit_refuses_an_unfinished_sign_in(aldwin_home: Path) -> None:
    staged = CredentialTokenStorage(FileCredentialStore(aldwin_home), "x", staged=True)
    with pytest.raises(ValueError):
        staged.commit(issuer="https://auth.example", metadata=None)


async def test_a_bearer_file_reads_as_empty(aldwin_home: Path) -> None:
    store = FileCredentialStore(aldwin_home)
    store.set("github", bearer_credential("t"))
    storage = CredentialTokenStorage(store, "github")
    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None
    assert storage.load_extras() == (None, None, None)


def _hold_lock(path: str, ready: object, release: object) -> None:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ready.set()  # type: ignore[attr-defined]
    release.wait(10)  # type: ignore[attr-defined]
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


async def test_fence_waits_for_another_process(aldwin_home: Path) -> None:
    directory = FileCredentialStore(aldwin_home).directory
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    proc = ctx.Process(
        target=_hold_lock,
        args=(str(directory / "linear.refresh.lock"), ready, release),
    )
    proc.start()
    try:
        assert await asyncio.to_thread(ready.wait, 10)
        with pytest.raises(TimeoutError):
            async with refresh_fence(directory, "linear", timeout=0.2):
                pass
        release.set()
        async with refresh_fence(directory, "linear", timeout=5):
            pass
    finally:
        release.set()
        proc.join(10)


async def test_fence_is_released_on_cancellation(aldwin_home: Path) -> None:
    directory = FileCredentialStore(aldwin_home).directory
    entered = asyncio.Event()

    async def hold() -> None:
        async with refresh_fence(directory, "linear"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold())
    await entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with refresh_fence(directory, "linear", timeout=0.2):
        pass
