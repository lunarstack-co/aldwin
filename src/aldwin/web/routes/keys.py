"""Request bodies and responses shared by the routes that check or save keys."""

from __future__ import annotations

from typing import Any, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import aldwin.client as client

MAX_KEY_CHARS = 1000


class KeyBody(BaseModel):
    """An API key for a model provider or a search provider."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["provider", "search"]
    name: str = Field(min_length=1, max_length=64)
    key: str = Field(min_length=1, max_length=MAX_KEY_CHARS)
    base_url: str | None = Field(default=None, max_length=2048)


def saved_key_response(
    body: KeyBody, outcome: client.KeyOutcome
) -> dict[str, Any] | JSONResponse:
    """Answer a save: the masked key on success, the reason and a 4xx/5xx if not."""
    if not outcome.ok:
        status = 502 if outcome.status == "unreachable" else 400
        return JSONResponse(
            {"detail": outcome.message, "status": outcome.status},
            status_code=status,
        )
    return {
        "ok": True,
        "kind": body.kind,
        "name": body.name.strip().lower(),
        "masked": outcome.masked,
        "env_override": outcome.env_override,
    }
