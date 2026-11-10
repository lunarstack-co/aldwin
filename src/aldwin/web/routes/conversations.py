"""``/v1/conversations``: saved conversations as JSON documents.

Opening, starting and running a conversation stay on the protocol; these
routes only read what is on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

import aldwin.client as client

NOT_FOUND_MESSAGE = "No saved conversation has that id."


def conversations_router(home: Path) -> APIRouter:
    """Build the routes that list saved conversations and return one file."""
    router = APIRouter(prefix="/conversations")

    @router.get("")
    def list_conversations() -> dict[str, Any]:
        return {"items": client.list_conversation_metas(home)}

    @router.get("/{session_id}")
    def get_conversation(session_id: str) -> dict[str, Any]:
        data = client.load_conversation_json(session_id, home)
        if data is None:
            raise HTTPException(status_code=404, detail=NOT_FOUND_MESSAGE)
        return data

    return router
