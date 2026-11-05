"""Unit and integration tests for provider key resolution and check-then-save."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from aldwin.core.approval_log import redact_secrets
from aldwin.core.providers import (
    AccessStatus,
    ModelInfo,
    save_provider_key,
)
from aldwin.core.providers.keys import resolve_provider_secret_field
from aldwin.core.settings import (
    AldwinConfig,
    ProviderConfig,
    SecretsConfig,
    load_secrets,
    resolve_key,
    save_secrets,
)
from aldwin.host.in_process import (
    save_provider_key as host_save_provider_key,
)
from aldwin.host.in_process import (
    verify_active_provider_key as host_verify_active_provider_key,
)


class DummyBackend:
    """Mock backend returning predetermined AccessStatus."""

    def __init__(self, status: AccessStatus) -> None:
        self._status = status
        self.check_access_called = False

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def check_access(self) -> AccessStatus:
        self.check_access_called = True
        return self._status

    def build_model(self, model_id: str) -> Any:
        raise NotImplementedError

    async def describe_model(self, model_id: str) -> ModelInfo | None:
        return None


def test_resolve_key_env_takes_precedence_and_never_writes_disk(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shell environment variable wins over file and is never persisted to disk."""
    secrets_path = aldwin_home / "secrets.yaml"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env-12345678")

    # 1. secrets.yaml is empty/missing
    res = resolve_key("anthropic", aldwin_home)
    assert res.key == "sk-ant-from-env-12345678"
    assert res.source == "env"
    assert not secrets_path.exists()

    # 2. secrets.yaml has a different key, env still wins
    save_secrets(
        SecretsConfig(anthropic_api_key=SecretStr("sk-ant-from-file-99999999")),
        aldwin_home,
    )
    res2 = resolve_key("anthropic", aldwin_home)
    assert res2.key == "sk-ant-from-env-12345678"
    assert res2.source == "env"

    # Disk file remains unchanged with its original file key
    loaded = load_secrets(aldwin_home)
    assert loaded.get_secret("anthropic") == "sk-ant-from-file-99999999"


def test_resolve_key_fallback_to_file_and_missing(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File fallback when environment variable is missing or whitespace."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "   ")

    save_secrets(
        SecretsConfig(
            gemini_api_key=SecretStr("AIzaSy1234567890abcdefghijklmnopqrstuvw")
        ),
        aldwin_home,
    )

    res_gemini = resolve_key("gemini", aldwin_home)
    assert res_gemini.key == "AIzaSy1234567890abcdefghijklmnopqrstuvw"
    assert res_gemini.source == "file"

    # Alias normalization
    res_gemini_alias = resolve_key("gemini_api_key", aldwin_home)
    assert res_gemini_alias.key == "AIzaSy1234567890abcdefghijklmnopqrstuvw"
    assert res_gemini_alias.source == "file"

    res_deepseek = resolve_key("deepseek", aldwin_home)
    assert res_deepseek.key is None
    assert res_deepseek.source == "missing"


async def test_save_provider_key_valid_persists_with_mode_0600(
    aldwin_home: Path,
) -> None:
    """Valid key check persists key to secrets.yaml with mode 0600."""
    dummy = DummyBackend(AccessStatus.OK)
    status = await save_provider_key(
        "anthropic",
        "sk-ant-valid-key-12345678",
        home=aldwin_home,
        backend=dummy,
    )
    assert status == AccessStatus.OK
    assert dummy.check_access_called

    secrets_path = aldwin_home / "secrets.yaml"
    assert secrets_path.exists()
    assert (secrets_path.stat().st_mode & 0o777) == 0o600

    loaded = load_secrets(aldwin_home)
    assert loaded.get_secret("anthropic") == "sk-ant-valid-key-12345678"


async def test_save_provider_key_invalid_leaves_secrets_untouched(
    aldwin_home: Path,
) -> None:
    """Invalid key check leaves secrets.yaml untouched."""
    dummy = DummyBackend(AccessStatus.INVALID_KEY)
    status = await save_provider_key(
        "anthropic",
        "sk-ant-bad-key-12345678",
        home=aldwin_home,
        backend=dummy,
    )
    assert status == AccessStatus.INVALID_KEY
    assert dummy.check_access_called

    secrets_path = aldwin_home / "secrets.yaml"
    assert not secrets_path.exists()


async def test_save_provider_key_error_statuses_leave_secrets_untouched(
    aldwin_home: Path,
) -> None:
    """Forbidden and timeout statuses leave secrets.yaml untouched."""
    dummy_no_access = DummyBackend(AccessStatus.NO_ACCESS)
    status_no_access = await save_provider_key(
        "gemini",
        "AIzaSy-noaccess-1234567890abcdefghijklm",
        home=aldwin_home,
        backend=dummy_no_access,
    )
    assert status_no_access == AccessStatus.NO_ACCESS

    dummy_unreachable = DummyBackend(AccessStatus.UNREACHABLE)
    status_unreachable = await save_provider_key(
        "openrouter",
        "sk-or-v1-unreachable-1234567890abcdef",
        home=aldwin_home,
        backend=dummy_unreachable,
    )
    assert status_unreachable == AccessStatus.UNREACHABLE

    secrets_path = aldwin_home / "secrets.yaml"
    assert not secrets_path.exists()


async def test_save_provider_key_empty_or_whitespace_key(aldwin_home: Path) -> None:
    """Empty or whitespace key returns invalid key without network call."""
    dummy = DummyBackend(AccessStatus.OK)
    status = await save_provider_key(
        "deepseek",
        "   ",
        home=aldwin_home,
        backend=dummy,
    )
    assert status == AccessStatus.INVALID_KEY
    assert not dummy.check_access_called

    secrets_path = aldwin_home / "secrets.yaml"
    assert not secrets_path.exists()


async def test_save_provider_key_exception_maps_to_unreachable(
    aldwin_home: Path,
) -> None:
    """Backend exception in check_access is caught and mapped to unreachable."""

    class FailingBackend(DummyBackend):
        async def check_access(self) -> AccessStatus:
            raise RuntimeError("simulated crash")

    status = await save_provider_key(
        "anthropic",
        "sk-ant-crash-key",
        home=aldwin_home,
        backend=FailingBackend(AccessStatus.OK),
    )
    assert status == AccessStatus.UNREACHABLE
    assert not (aldwin_home / "secrets.yaml").exists()


def test_resolve_provider_secret_field_fallback() -> None:
    """Secret field resolves to normalized key name for unknown provider."""
    assert resolve_provider_secret_field("unknown_vendor") == "unknown_vendor_api_key"
    assert resolve_provider_secret_field("custom-server") == "custom_server_api_key"


async def test_save_provider_key_custom_provider_field(aldwin_home: Path) -> None:
    """Custom provider secret field name is preserved and saved."""
    cfg = AldwinConfig(
        providers={
            "office-gpu": ProviderConfig(
                shape="openai_chat",
                base_url="https://gpu.internal/v1",
                secret="gpu_access_token",
            )
        }
    )
    dummy = DummyBackend(AccessStatus.OK)
    status = await save_provider_key(
        "office-gpu",
        "gpu-token-xyz-123",
        home=aldwin_home,
        config=cfg,
        backend=dummy,
    )
    assert status == AccessStatus.OK

    secrets_path = aldwin_home / "secrets.yaml"
    raw = yaml.safe_load(secrets_path.read_text(encoding="utf-8"))
    assert raw == {"gpu_access_token": "gpu-token-xyz-123"}


async def test_save_provider_key_uses_custom_base_url(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing base_url overrides provider default base_url on constructed backend."""
    captured: list[Any] = []

    def fake_get_backend(
        provider: Any,
        config: Any = None,
        secrets: Any = None,
        home: Any = None,
        transport: Any = None,
    ) -> Any:
        captured.append(provider)
        return DummyBackend(AccessStatus.OK)

    monkeypatch.setattr("aldwin.core.providers.keys.get_backend", fake_get_backend)

    status = await save_provider_key(
        "anthropic",
        "sk-ant-test-key",
        home=aldwin_home,
        base_url="https://custom.anthropic.internal/v1",
    )
    assert status == AccessStatus.OK
    assert len(captured) == 1
    assert captured[0].base_url == "https://custom.anthropic.internal/v1"


async def test_save_provider_key_rejects_local_provider(aldwin_home: Path) -> None:
    """Local providers without secrets raise ValueError."""
    dummy = DummyBackend(AccessStatus.OK)
    with pytest.raises(ValueError) as exc_info:
        await save_provider_key("ollama", "fake-token", home=aldwin_home, backend=dummy)
    assert "does not use an API key" in str(exc_info.value)


async def test_save_provider_key_preserves_existing_keys(aldwin_home: Path) -> None:
    """Saving a key for a new provider preserves existing stored keys."""
    save_secrets(
        SecretsConfig(openai_api_key=SecretStr("sk-openai-existing-12345")),
        aldwin_home,
    )

    dummy = DummyBackend(AccessStatus.OK)
    status = await save_provider_key(
        "gemini",
        "AIzaSy1234567890abcdefghijklmnopqrstuvw",
        home=aldwin_home,
        backend=dummy,
    )
    assert status == AccessStatus.OK

    loaded = load_secrets(aldwin_home)
    assert loaded.get_secret("openai") == "sk-openai-existing-12345"
    assert loaded.get_secret("gemini") == "AIzaSy1234567890abcdefghijklmnopqrstuvw"


async def test_host_save_provider_key_bridge(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host helper bridges save_provider_key to core without importing core."""
    called: list[tuple[str, str]] = []

    async def fake_save(
        provider: str,
        key: str,
        *,
        home: Path | None = None,
        base_url: str | None = None,
        config: Any = None,
        backend: Any = None,
    ) -> AccessStatus:
        called.append((provider, key))
        return AccessStatus.OK

    monkeypatch.setattr("aldwin.core.providers.keys.save_provider_key", fake_save)
    status = await host_save_provider_key(
        "anthropic",
        "sk-ant-test-bridge-1234",
        home=aldwin_home,
    )
    assert status == AccessStatus.OK
    assert called == [("anthropic", "sk-ant-test-bridge-1234")]


async def test_host_verify_active_provider_key_bridge(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host verify_active_provider_key checks active key status."""
    missing_status = await host_verify_active_provider_key(
        "anthropic", home=aldwin_home
    )
    assert missing_status == AccessStatus.INVALID_KEY

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-active")
    monkeypatch.setattr(
        "aldwin.core.providers.factory.get_backend",
        lambda *args, **kwargs: DummyBackend(AccessStatus.OK),
    )
    ok_status = await host_verify_active_provider_key("anthropic", home=aldwin_home)
    assert ok_status == AccessStatus.OK


def test_approval_log_redacts_gemini_key() -> None:
    """redact_secrets redacts Google Gemini AIza keys."""
    gemini_key = "AIzaSy" + "1234567890abcdef" * 2 + "x"  # 39 chars total
    res = redact_secrets(f"curl -H 'x-goog-api-key: {gemini_key}'")
    assert gemini_key not in res
    assert "‹redacted›" in res
