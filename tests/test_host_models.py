"""Tests for host model listing, cache, keys, and custom servers."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
from fakes.model import FakeModel
from pydantic import SecretStr

from aldwin.client import AldwinClient, ProtocolError
from aldwin.core.agent import build_agent
from aldwin.core.providers.discovery import get_discovery_cache
from aldwin.core.settings import (
    AldwinConfig,
    ConfigError,
    ProviderConfig,
    SecretsConfig,
    load_config,
    load_secrets,
    save_secrets,
    update_config_provider,
)
from aldwin.host.in_process import InProcessTransport
from aldwin.host.runner import AldwinHost
from aldwin.protocol import (
    DoneEvent,
    ErrorEvent,
    ModelsEvent,
)


@pytest.fixture
def clean_discovery_cache() -> None:
    """Clear discovery global cache between test runs."""
    get_discovery_cache()._cache.clear()


def make_host(
    transport: InProcessTransport,
    home: Path,
    fake_model: FakeModel,
    backend_transport: httpx.AsyncBaseTransport | None = None,
) -> AldwinHost:
    return AldwinHost(
        transport,
        home=home,
        agent_builder=lambda m, ask: build_agent(
            AldwinConfig(model=m), SecretsConfig(), ask, home=home, model=fake_model
        ),
        backend_transport=backend_transport,
    )


async def test_client_list_models_over_protocol(
    aldwin_home: Path,
    fake_model: FakeModel,
    clean_discovery_cache: None,
) -> None:
    """Client list_models sends request and receives ModelsEvent."""
    transport = InProcessTransport()
    host = make_host(transport, aldwin_home, fake_model)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()

    try:
        event = await client.list_models()
        assert isinstance(event, ModelsEvent)
        assert event.current == "openai/gpt-4o"
        assert any(p.name == "openai" for p in event.providers)
        assert any(p.name == "ollama" for p in event.providers)
    finally:
        await client.close()
        await host.stop_host()


async def test_host_models_list_concurrent_and_cached(
    aldwin_home: Path,
    fake_model: FakeModel,
    clean_discovery_cache: None,
) -> None:
    """Concurrent listing, 10 min cache, refresh override."""

    async def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "/api/tags" in url_str:
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:8b"}]},
            )
        if "/api/show" in url_str:
            return httpx.Response(
                200,
                json={
                    "capabilities": ["tools"],
                    "model_info": {"general.context_length": 32768},
                },
            )
        if "/models" in url_str:
            return httpx.Response(
                200,
                json={"data": [{"id": "gpt-4o"}]},
            )
        return httpx.Response(404)

    mock_transport = httpx.MockTransport(handler)
    transport = InProcessTransport()
    host = make_host(
        transport, aldwin_home, fake_model, backend_transport=mock_transport
    )
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()

    try:
        event1 = await client.list_models(refresh=True)
        assert isinstance(event1, ModelsEvent)
        ollama_p = next(p for p in event1.providers if p.name == "ollama")
        assert ollama_p.status == "ok"
        assert len(ollama_p.models) == 1
        assert ollama_p.models[0].id == "qwen3:8b"
        assert ollama_p.models[0].supports_tools is True
        assert ollama_p.models[0].context_window == 32768

        # Cache hit returns immediately
        event2 = await client.list_models(refresh=False)
        assert event2.providers == event1.providers

        # Force refresh re-fetches
        event3 = await client.list_models(refresh=True)
        assert isinstance(event3, ModelsEvent)
    finally:
        await client.close()
        await host.stop_host()


async def test_host_models_list_transient_failure_no_flicker(
    aldwin_home: Path,
    fake_model: FakeModel,
    clean_discovery_cache: None,
) -> None:
    """Transient probe failure retains cached models without flickering."""
    should_fail = False

    async def handler(request: httpx.Request) -> httpx.Response:
        if should_fail:
            raise httpx.ConnectError("Connection refused")
        if "/api/tags" in str(request.url):
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        if "/api/show" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "capabilities": ["tools"],
                    "model_info": {"general.context_length": 32768},
                },
            )
        return httpx.Response(200, json={"data": []})

    mock_transport = httpx.MockTransport(handler)
    transport = InProcessTransport()
    host = make_host(
        transport, aldwin_home, fake_model, backend_transport=mock_transport
    )
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()

    try:
        ev1 = await client.list_models(refresh=True)
        ollama1 = next(p for p in ev1.providers if p.name == "ollama")
        assert ollama1.status == "ok"
        assert len(ollama1.models) == 1

        # Simulate outage
        should_fail = True
        ev2 = await client.list_models(refresh=True)
        ollama2 = next(p for p in ev2.providers if p.name == "ollama")
        assert ollama2.status == "not_running"
        assert len(ollama2.models) == 1
        assert ollama2.models[0].id == "qwen3:8b"
    finally:
        await client.close()
        await host.stop_host()


async def test_host_models_list_slow_provider_timeout(
    aldwin_home: Path,
    fake_model: FakeModel,
    clean_discovery_cache: None,
) -> None:
    """Slow provider times out without failing overall listing."""

    async def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "anthropic" in url_str:
            raise httpx.ConnectTimeout("Timed out connecting to Anthropic")
        return httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})

    save_secrets(SecretsConfig(anthropic_api_key=SecretStr("sk-ant-test")), aldwin_home)

    mock_transport = httpx.MockTransport(handler)
    transport = InProcessTransport()
    host = make_host(
        transport, aldwin_home, fake_model, backend_transport=mock_transport
    )
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()

    try:
        start = time.monotonic()
        ev = await client.list_models(refresh=True)
        elapsed = time.monotonic() - start
        anthropic_p = next(p for p in ev.providers if p.name == "anthropic")
        assert anthropic_p.status == "unreachable"
        assert elapsed < 6.0
    finally:
        await client.close()
        await host.stop_host()


async def test_host_models_list_unknown_provider_error(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Requesting an unknown provider returns a structured not_found error."""
    transport = InProcessTransport()
    host = make_host(transport, aldwin_home, fake_model)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()

    try:
        with pytest.raises(ProtocolError) as exc_info:
            await client.list_models(provider="nonexistent")
        assert exc_info.value.code == "not_found"
        assert "nonexistent" in str(exc_info.value)
    finally:
        await client.close()
        await host.stop_host()


async def test_provider_key_valid_and_invalid(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Provider key saves valid key, rejects bad key, hides secret."""

    async def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization", "")
        if "sk-valid" in auth:
            return httpx.Response(200, json={"data": [{"id": "deepseek-chat"}]})
        return httpx.Response(401, json={"error": "Invalid API key"})

    mock_transport = httpx.MockTransport(handler)
    transport = InProcessTransport()
    host = make_host(
        transport, aldwin_home, fake_model, backend_transport=mock_transport
    )
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()

    try:
        bad_res = await client.save_provider_key("deepseek", "sk-bad-key")
        assert isinstance(bad_res, ErrorEvent)
        assert bad_res.code == "auth_failed"
        assert "sk-bad-key" not in bad_res.message
        assert load_secrets(aldwin_home).get_secret("deepseek_api_key") is None

        ok_res = await client.save_provider_key("deepseek", "sk-valid-key")
        assert isinstance(ok_res, DoneEvent)
        assert (
            load_secrets(aldwin_home).get_secret("deepseek_api_key") == "sk-valid-key"
        )
    finally:
        await client.close()
        await host.stop_host()


async def test_provider_server_add_and_validation(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Provider server registers valid server, rejects invalid URL."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "custom-model"}]})

    mock_transport = httpx.MockTransport(handler)
    transport = InProcessTransport()
    host = make_host(
        transport, aldwin_home, fake_model, backend_transport=mock_transport
    )
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()

    try:
        bad_res = await client.add_provider_server("bad", "ftp://invalid-server")
        assert isinstance(bad_res, ErrorEvent)
        assert bad_res.code == "invalid_request"

        ok_res = await client.add_provider_server(
            "office-gpu",
            "http://127.0.0.1:8000/v1",
            title="Office GPU",
            local=True,
        )
        assert isinstance(ok_res, DoneEvent)
        cfg = load_config(aldwin_home)
        assert "office-gpu" in cfg.providers
        assert cfg.providers["office-gpu"].base_url == "http://127.0.0.1:8000/v1"

        ev = await client.list_models(refresh=True)
        custom_p = next(p for p in ev.providers if p.name == "office-gpu")
        assert custom_p.status == "ok"
        assert len(custom_p.models) == 1
        assert custom_p.models[0].id == "custom-model"
    finally:
        await client.close()
        await host.stop_host()


def test_update_config_provider_rejects_empty_name(aldwin_home: Path) -> None:
    """Empty provider names are rejected with ConfigError."""
    with pytest.raises(ConfigError) as exc_info:
        update_config_provider(
            "   ",
            ProviderConfig(base_url="http://127.0.0.1:8000"),
            aldwin_home,
        )
    assert "providers" in str(exc_info.value)


async def test_set_model_resolves_bare_id(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Set model bare ID resolves to `openai/<id>`."""
    transport = InProcessTransport()
    host = make_host(transport, aldwin_home, fake_model)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()

    try:
        res = await client.set_model("gpt-5")
        assert isinstance(res, DoneEvent)
        assert client.model == "openai/gpt-5"
    finally:
        await client.close()
        await host.stop_host()
