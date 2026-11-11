"""``/v1/setup``: first-run helpers that work before any model key exists."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

import aldwin.client as client
from aldwin.web.routes.keys import MAX_KEY_CHARS, KeyBody, saved_key_response
from aldwin.web.runtime import WebHostRuntime


class ProviderKeyBody(BaseModel):
    """A built-in provider's key, or a server address with an optional key."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    key: str | None = Field(default=None, min_length=1, max_length=MAX_KEY_CHARS)
    base_url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def _key_or_server(self) -> ProviderKeyBody:
        if self.key is None and not self.base_url:
            raise ValueError("Enter an API key, or a server address.")
        return self


class ModelsBody(BaseModel):
    """The provider whose models to list."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=64)


class SetupBody(BaseModel):
    """The terminal's first-run answers: an OpenAI key, a Tavily key and a model."""

    model_config = ConfigDict(extra="forbid")

    openai_api_key: str | None = Field(default=None, max_length=MAX_KEY_CHARS)
    tavily_api_key: str | None = Field(default=None, max_length=MAX_KEY_CHARS)
    model: str | None = Field(default=None, max_length=200)


class ProfileBody(BaseModel):
    """One answer of the optional profile step, the finish, or a skip."""

    model_config = ConfigDict(extra="forbid")

    first_name: str | None = None
    occupation: str | None = None
    personality: list[str] | None = Field(default=None, max_length=10)
    complete: bool = False
    skipped: bool = False


def setup_router(home: Path, runtime: WebHostRuntime) -> APIRouter:
    """Build the first-run routes; saving working keys starts the host."""
    router = APIRouter(prefix="/setup")

    def status_with_host() -> dict[str, Any]:
        status = client.get_setup_status(home)
        status["host_ready"] = runtime.ready
        return status

    @router.get("/status")
    def status() -> dict[str, Any]:
        return status_with_host()

    @router.post("/check-key")
    async def check_key(body: KeyBody) -> dict[str, Any]:
        outcome = await client.check_key(
            body.kind, body.name, body.key, home=home, base_url=body.base_url
        )
        return {"ok": outcome.ok, "status": outcome.status, "message": outcome.message}

    @router.post("/provider-key", response_model=None)
    async def provider_key(body: ProviderKeyBody) -> dict[str, Any] | JSONResponse:
        if body.base_url:
            outcome = await client.connect_server(
                body.name, body.base_url, body.key, home=home
            )
        else:
            key = cast(str, body.key)
            outcome = await client.save_key("provider", body.name, key, home=home)
        if outcome.ok:
            await runtime.start_host()
        return saved_key_response("provider", body.name, outcome)

    @router.post("/detect-local")
    async def detect_local() -> dict[str, Any]:
        found = await client.detect_local_providers(home=home)
        return {"items": [asdict(item) for item in found]}

    @router.post("/models")
    async def models(body: ModelsBody) -> dict[str, Any]:
        found = await client.list_models_for(body.provider.strip().lower(), home)
        return {"items": [asdict(item) for item in found]}

    @router.post("/save", response_model=None)
    async def save(body: SetupBody) -> dict[str, Any] | JSONResponse:
        keys: tuple[tuple[Literal["provider", "search"], str, str], ...] = (
            ("provider", "openai", (body.openai_api_key or "").strip()),
            ("search", "tavily", (body.tavily_api_key or "").strip()),
        )
        for kind, name, key in keys:
            if not key:
                continue
            outcome = await client.check_key(kind, name, key, home=home)
            if not outcome.ok:
                code = 502 if outcome.status == "unreachable" else 400
                return JSONResponse(
                    {"detail": outcome.message, "status": outcome.status},
                    status_code=code,
                )
        # Keys first: a failed key write then leaves the model as it was.
        await run_in_threadpool(
            lambda: client.save_setup(
                openai_api_key=keys[0][2] or None,
                tavily_api_key=keys[1][2] or None,
                home=home,
            )
        )
        model = (body.model or "").strip()
        if model:
            await run_in_threadpool(client.apply_settings_patch, {"model": model}, home)
        await runtime.start_host()
        return status_with_host()

    @router.post("/profile")
    def profile(body: ProfileBody) -> dict[str, Any]:
        fields = body.model_dump(exclude={"complete", "skipped"}, exclude_none=True)
        client.save_onboarding_profile(
            fields, complete=body.complete, skipped=body.skipped, home=home
        )
        return status_with_host()

    return router
