"""Local model runtime discovery (Ollama and LM Studio) and inventory cache."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from aldwin.core.providers.base import ModelInfo
from aldwin.core.providers.ollama import parse_ollama_show_response

logger = logging.getLogger("aldwin.core.providers.discovery")

_DEFAULT_PROBE_TIMEOUT: float = 1.0
_DEFAULT_CACHE_TTL: float = 600.0  # 10 minutes cache retention across transient outages


@dataclass(frozen=True, slots=True)
class LocalProviderStatus:
    """Status and available models for one local provider endpoint."""

    name: str  # "ollama" | "lmstudio"
    title: str  # "Ollama" | "LM Studio"
    base_url: str
    running: bool
    models: tuple[ModelInfo, ...]


class LocalInventoryCache:
    """Retains last successful inventory across transient probe outages."""

    def __init__(self, ttl_seconds: float = _DEFAULT_CACHE_TTL) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, tuple[ModelInfo, ...]]] = {}

    def get(self, name: str) -> tuple[ModelInfo, ...] | None:
        entry = self._cache.get(name)
        if entry is None:
            return None
        _ts, models = entry
        return models

    def is_fresh(self, name: str, now: float | None = None) -> bool:
        entry = self._cache.get(name)
        if entry is None:
            return False
        ts, _models = entry
        current = time.monotonic() if now is None else now
        return (current - ts) < self._ttl

    def update(
        self,
        name: str,
        models: Sequence[ModelInfo],
        now: float | None = None,
    ) -> None:
        current = time.monotonic() if now is None else now
        self._cache[name] = (current, tuple(models))


_GLOBAL_CACHE = LocalInventoryCache()


async def _probe_ollama_model_show(
    client: httpx.AsyncClient,
    model_name: str,
) -> ModelInfo:
    """Fetch tool capabilities and context length for a single Ollama model."""
    try:
        resp = await client.post(
            "/api/show",
            json={"model": model_name, "name": model_name},
        )
        if resp.status_code == 200:
            data = resp.json()
            supports_tools, context_window = parse_ollama_show_response(data)
            return ModelInfo(
                id=model_name,
                title=model_name,
                context_window=context_window,
                supports_tools=supports_tools,
                local=True,
            )
    except Exception as exc:
        logger.debug("Failed /api/show for %s: %s", model_name, exc)

    return ModelInfo(
        id=model_name,
        title=model_name,
        context_window=2048,
        supports_tools=None,
        local=True,
    )


async def _probe_ollama_raw(
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
    cache: LocalInventoryCache,
) -> LocalProviderStatus:
    base_url = "http://127.0.0.1:11434"
    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        ) as client:
            resp = await client.get("/api/tags")
            if resp.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"Status {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            payload = resp.json()
            raw_models = payload.get("models", []) if isinstance(payload, dict) else []
            names: list[str] = []
            for item in raw_models:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("model")
                    if isinstance(name, str) and name.strip():
                        names.append(name.strip())

            # Concurrently describe models with the remaining client timeout
            model_infos = await asyncio.gather(
                *[_probe_ollama_model_show(client, name) for name in names]
            )
            models_tuple = tuple(model_infos)
            cache.update("ollama", models_tuple)
            return LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url=base_url,
                running=True,
                models=models_tuple,
            )
    except Exception as exc:
        logger.debug("Ollama probe failed: %s", exc)
        cached = cache.get("ollama")
        return LocalProviderStatus(
            name="ollama",
            title="Ollama",
            base_url=base_url,
            running=False,
            models=cached if cached is not None else (),
        )


async def _probe_ollama(
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
    cache: LocalInventoryCache,
) -> LocalProviderStatus:
    try:
        return await asyncio.wait_for(
            _probe_ollama_raw(timeout=timeout, transport=transport, cache=cache),
            timeout=timeout,
        )
    except (TimeoutError, asyncio.TimeoutError):
        logger.debug("Ollama probe timed out after %.2fs", timeout)
        cached = cache.get("ollama")
        return LocalProviderStatus(
            name="ollama",
            title="Ollama",
            base_url="http://127.0.0.1:11434",
            running=False,
            models=cached if cached is not None else (),
        )


async def _probe_lmstudio_raw(
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
    cache: LocalInventoryCache,
) -> LocalProviderStatus:
    base_url = "http://127.0.0.1:1234/v1"
    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        ) as client:
            resp = await client.get("/models")
            if resp.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"Status {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            payload = resp.json()
            raw_data = payload.get("data", []) if isinstance(payload, dict) else []
            models: list[ModelInfo] = []
            for item in raw_data:
                if not isinstance(item, dict):
                    continue
                model_id = item.get("id") or item.get("name")
                if not isinstance(model_id, str) or not model_id.strip():
                    continue

                capabilities = item.get("capabilities")
                supports_tools: bool | None = None
                if (
                    isinstance(capabilities, dict)
                    and "trained_for_tool_use" in capabilities
                ):
                    supports_tools = bool(capabilities["trained_for_tool_use"])

                ctx_length = 2048
                if isinstance(item.get("max_context_length"), (int, float)):
                    ctx_length = int(item["max_context_length"])

                models.append(
                    ModelInfo(
                        id=model_id,
                        title=model_id,
                        context_window=ctx_length,
                        supports_tools=supports_tools,
                        local=True,
                    )
                )

            models_tuple = tuple(models)
            cache.update("lmstudio", models_tuple)
            return LocalProviderStatus(
                name="lmstudio",
                title="LM Studio",
                base_url=base_url,
                running=True,
                models=models_tuple,
            )
    except Exception as exc:
        logger.debug("LM Studio probe failed: %s", exc)
        cached = cache.get("lmstudio")
        return LocalProviderStatus(
            name="lmstudio",
            title="LM Studio",
            base_url=base_url,
            running=False,
            models=cached if cached is not None else (),
        )


async def _probe_lmstudio(
    *,
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
    cache: LocalInventoryCache,
) -> LocalProviderStatus:
    try:
        return await asyncio.wait_for(
            _probe_lmstudio_raw(timeout=timeout, transport=transport, cache=cache),
            timeout=timeout,
        )
    except (TimeoutError, asyncio.TimeoutError):
        logger.debug("LM Studio probe timed out after %.2fs", timeout)
        cached = cache.get("lmstudio")
        return LocalProviderStatus(
            name="lmstudio",
            title="LM Studio",
            base_url="http://127.0.0.1:1234/v1",
            running=False,
            models=cached if cached is not None else (),
        )


async def detect_local_providers(
    *,
    timeout: float = _DEFAULT_PROBE_TIMEOUT,
    force_refresh: bool = False,
    cache: LocalInventoryCache | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[LocalProviderStatus]:
    """Probe loopback addresses of Ollama (:11434) and LM Studio (:1234) concurrently.

    Does NOT probe Jan (:1337) or any non-loopback address.
    """
    active_cache = cache if cache is not None else _GLOBAL_CACHE
    if (
        not force_refresh
        and active_cache.is_fresh("ollama")
        and active_cache.is_fresh("lmstudio")
    ):
        ollama_cached = active_cache.get("ollama") or ()
        lmstudio_cached = active_cache.get("lmstudio") or ()
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=True,
                models=ollama_cached,
            ),
            LocalProviderStatus(
                name="lmstudio",
                title="LM Studio",
                base_url="http://127.0.0.1:1234/v1",
                running=True,
                models=lmstudio_cached,
            ),
        ]

    ollama_task = _probe_ollama(
        timeout=timeout, transport=transport, cache=active_cache
    )
    lmstudio_task = _probe_lmstudio(
        timeout=timeout, transport=transport, cache=active_cache
    )

    results = await asyncio.gather(ollama_task, lmstudio_task)
    return list(results)
