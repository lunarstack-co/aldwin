"""``/v1/settings``: read and change safe settings, and save keys write-only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

import aldwin.client as client
from aldwin.web.routes.keys import KeyBody, saved_key_response
from aldwin.web.runtime import WebHostRuntime


def settings_router(home: Path, runtime: WebHostRuntime) -> APIRouter:
    """Build the settings routes; a change that makes a model usable starts the host."""
    router = APIRouter(prefix="/settings")

    @router.get("")
    def get_settings() -> dict[str, Any]:
        return client.get_settings_view(home)

    @router.patch("")
    async def patch_settings(patch: dict[str, Any] = Body(...)) -> dict[str, Any]:
        view = await run_in_threadpool(client.apply_settings_patch, patch, home)
        await runtime.start_host()
        return view

    @router.post("/secrets", response_model=None)
    async def save_secret(body: KeyBody) -> dict[str, Any] | JSONResponse:
        outcome = await client.save_key(
            body.kind, body.name, body.key, home=home, base_url=body.base_url
        )
        if outcome.ok:
            await runtime.start_host()
        return saved_key_response(body.kind, body.name, outcome)

    return router
