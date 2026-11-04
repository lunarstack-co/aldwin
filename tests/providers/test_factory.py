"""Unit tests for ProviderBackend factory."""

from pathlib import Path

import pytest

from aldwin.core.providers.base import ProviderBackend
from aldwin.core.providers.factory import get_backend
from aldwin.core.providers.openai_chat import OpenAIChatBackend
from aldwin.core.settings import (
    AldwinConfig,
    ConfigError,
    ProviderConfig,
    SecretsConfig,
)


def test_factory_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    backend = get_backend("openai")
    assert isinstance(backend, ProviderBackend)
    assert isinstance(backend, OpenAIChatBackend)
    assert backend.base_url == "https://api.openai.com/v1"
    assert backend.api_key == "sk-test-openai"


def test_factory_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    backend = get_backend("anthropic")
    assert isinstance(backend, ProviderBackend)
    from aldwin.core.providers.anthropic import AnthropicBackend

    assert isinstance(backend, AnthropicBackend)
    assert backend.base_url == "https://api.anthropic.com"
    assert backend.api_key == "sk-ant-test"


def test_factory_ollama() -> None:
    backend = get_backend("ollama")
    assert isinstance(backend, ProviderBackend)
    from aldwin.core.providers.ollama import OllamaBackend

    assert isinstance(backend, OllamaBackend)
    assert backend.base_url == "http://localhost:11434"
    assert backend.api_key is None


def test_factory_custom_openai_chat() -> None:
    config = AldwinConfig(
        providers={
            "office-gpu": ProviderConfig(
                shape="openai_chat",
                base_url="http://192.168.1.50:8000/v1",
                secret="office_gpu_key",
                local=True,
            )
        }
    )
    secrets = SecretsConfig(office_gpu_key="secret-token")
    backend = get_backend("office-gpu", config=config, secrets=secrets)
    assert isinstance(backend, OpenAIChatBackend)
    assert backend.base_url == "http://192.168.1.50:8000/v1"
    assert backend.api_key == "secret-token"


def test_factory_unsupported_shape() -> None:
    from aldwin.core.providers.registry import ProviderEntry

    entry = ProviderEntry(
        name="custom",
        title="Custom",
        shape="unsupported",  # type: ignore[arg-type]
        base_url="http://localhost:8000",
        secret=None,
        local=True,
        docs_url="",
    )
    with pytest.raises(ConfigError) as exc_info:
        get_backend(entry)
    assert "providers.custom.shape" in str(exc_info.value)


def test_factory_resolves_key_from_home_secrets(aldwin_home: Path) -> None:
    from aldwin.core.settings import save_secrets

    save_secrets(SecretsConfig(anthropic_api_key="disk-key"), home=aldwin_home)
    backend = get_backend("anthropic", home=aldwin_home)
    assert getattr(backend, "api_key", None) == "disk-key"


def test_resolve_provider_key_empty_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aldwin.core.providers.factory import resolve_provider_key
    from aldwin.core.providers.registry import get_provider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    entry = get_provider("anthropic")
    assert entry is not None
    secrets = SecretsConfig(anthropic_api_key="fallback-secret")
    key = resolve_provider_key(entry, secrets=secrets)
    assert key == "fallback-secret"


def test_registry_model_ref_and_loopback_helpers() -> None:
    from aldwin.core.providers.registry import (
        ModelRef,
        is_loopback_host,
        parse_model_ref,
    )

    ref = ModelRef("openai", "gpt-4o")
    assert str(ref) == "openai/gpt-4o"
    assert is_loopback_host("") is False
    assert is_loopback_host("[::1]") is True
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("example.com") is False

    with pytest.raises(ValueError):
        parse_model_ref("")

    with pytest.raises(ValueError):
        parse_model_ref("openai/")
