"""Unit tests for the local provider finder and inventory cache."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from aldwin.core.providers.base import ModelInfo
from aldwin.core.providers.discovery import (
    LocalInventoryCache,
    detect_local_providers,
)


@pytest.mark.asyncio
async def test_probe_budget_under_timeout() -> None:
    """Probes to Ollama and LM Studio enforce 1.0s timeout concurrently."""

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2.0)
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(slow_handler)
    start = time.monotonic()
    statuses = await detect_local_providers(
        timeout=1.0,
        transport=transport,
        cache=LocalInventoryCache(),
    )
    duration = time.monotonic() - start

    assert duration <= 1.15, f"Probe took {duration:.2f}s, expected <= 1.15s"
    assert len(statuses) == 2
    for s in statuses:
        assert s.running is False
        assert s.models == ()


@pytest.mark.asyncio
async def test_probe_excludes_jan_and_port_1337() -> None:
    """Port 1337 and name 'jan' are never probed by the finder."""
    attempted_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        attempted_urls.append(url_str)
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    await detect_local_providers(transport=transport, cache=LocalInventoryCache())

    assert len(attempted_urls) > 0
    for url in attempted_urls:
        assert ":1337" not in url, f"Jan port 1337 was probed: {url}"
        assert "jan" not in url.lower(), f"Jan path/host was probed: {url}"


@pytest.mark.asyncio
async def test_ollama_show_metadata_extraction() -> None:
    """Ollama tags and show extract tool capability and context length."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:8b"},
                        {"name": "gemma3:4b"},
                        {"name": "legacy:7b"},
                    ]
                },
            )
        if path == "/api/show":
            body = json.loads(request.content.decode("utf-8"))
            model_name = body.get("model") or body.get("name")
            if model_name == "qwen3:8b":
                return httpx.Response(
                    200,
                    json={
                        "capabilities": ["tools", "vision"],
                        "model_info": {"qwen3.context_length": 32768},
                    },
                )
            if model_name == "gemma3:4b":
                return httpx.Response(
                    200,
                    json={
                        "capabilities": [],
                        "model_info": {"gemma3.context_length": 131072},
                    },
                )
            if model_name == "legacy:7b":
                return httpx.Response(200, json={})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    statuses = await detect_local_providers(
        transport=transport,
        cache=LocalInventoryCache(),
    )
    ollama_status = next((s for s in statuses if s.name == "ollama"), None)
    assert ollama_status is not None
    assert ollama_status.running is True

    model_map = {m.id: m for m in ollama_status.models}
    assert "qwen3:8b" in model_map
    assert model_map["qwen3:8b"].supports_tools is True
    assert model_map["qwen3:8b"].context_window == 32768

    assert "gemma3:4b" in model_map
    assert model_map["gemma3:4b"].supports_tools is False
    assert model_map["gemma3:4b"].context_window == 131072

    assert "legacy:7b" in model_map
    assert model_map["legacy:7b"].supports_tools is None


@pytest.mark.asyncio
async def test_lmstudio_metadata_extraction() -> None:
    """LM Studio /v1/models extracts tool capability and context limits."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen2.5-coder-7b",
                            "capabilities": {"trained_for_tool_use": True},
                            "max_context_length": 65536,
                        },
                        {
                            "id": "deepseek-coder-6.7b",
                            "capabilities": {"trained_for_tool_use": False},
                            "max_context_length": 16384,
                        },
                        {
                            "id": "plain-model",
                        },
                    ]
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    statuses = await detect_local_providers(
        transport=transport,
        cache=LocalInventoryCache(),
    )
    lm_status = next((s for s in statuses if s.name == "lmstudio"), None)
    assert lm_status is not None
    assert lm_status.running is True

    models = {m.id: m for m in lm_status.models}
    assert models["qwen2.5-coder-7b"].supports_tools is True
    assert models["qwen2.5-coder-7b"].context_window == 65536

    assert models["deepseek-coder-6.7b"].supports_tools is False
    assert models["deepseek-coder-6.7b"].context_window == 16384

    assert models["plain-model"].supports_tools is None
    assert models["plain-model"].context_window == 2048


@pytest.mark.asyncio
async def test_cache_retained_on_transient_error() -> None:
    """Transient connection drop retains prior cached model inventory."""
    cache = LocalInventoryCache(ttl_seconds=600.0)
    cached_models = (
        ModelInfo(
            id="qwen3:8b",
            title="qwen3:8b",
            context_window=32768,
            supports_tools=True,
            local=True,
        ),
    )
    cache.update("ollama", cached_models)

    # Server now fails with ConnectionError
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(failing_handler)
    statuses = await detect_local_providers(transport=transport, cache=cache)
    ollama_status = next((s for s in statuses if s.name == "ollama"), None)

    assert ollama_status is not None
    assert ollama_status.running is False
    # Models are retained from cache rather than emptied
    assert ollama_status.models == cached_models
