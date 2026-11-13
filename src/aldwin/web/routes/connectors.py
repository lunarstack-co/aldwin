"""``/v1/connectors/{name}/icon``: a connected app's own icon, from the home folder."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

import aldwin.client as client

# An SVG opened on its own must not run scripts or load anything.
ICON_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, max-age=31536000, immutable",
}


def connectors_router(home: Path) -> APIRouter:
    """Build the route that serves a connector's saved icon."""
    router = APIRouter(prefix="/connectors")

    @router.get("/{name}/icon")
    def get_icon(name: str) -> Response:
        icon = client.load_connector_icon(name, home)
        if icon is None:
            raise HTTPException(
                status_code=404, detail="No icon is saved for that app."
            )
        data, media_type = icon
        return Response(content=data, media_type=media_type, headers=ICON_HEADERS)

    return router
