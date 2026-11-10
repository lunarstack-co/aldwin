"""``/v1/settings``: read and change safe settings, and save keys write-only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

import aldwin.client as client
from aldwin.web.routes.keys import KeyBody, saved_key_response


def settings_router(home: Path) -> APIRouter:
    """Build the settings routes for the Aldwin home at ``home``."""
    router = APIRouter(prefix="/settings")

    @router.get("")
    def get_settings() -> dict[str, Any]:
        return client.get_settings_view(home)

    @router.patch("")
    def patch_settings(patch: dict[str, Any] = Body(...)) -> dict[str, Any]:
        return client.apply_settings_patch(patch, home)

    @router.post("/secrets", response_model=None)
    async def save_secret(body: KeyBody) -> dict[str, Any] | JSONResponse:
        outcome = await client.save_key(
            body.kind, body.name, body.key, home=home, base_url=body.base_url
        )
        return saved_key_response(body, outcome)

    return router
