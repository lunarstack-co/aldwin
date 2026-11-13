"""Unit tests for the provider catalogue, model reference parser, and registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from aldwin.core.home import atomic_write, ensure_home
from aldwin.core.providers.registry import (
    ModelRef,
    ProviderEntry,
    get_provider,
    is_loopback_host,
    list_providers,
    load_builtin_providers,
    parse_model_ref,
    validate_model_ref,
)
from aldwin.core.settings import (
    AldwinConfig,
    ConfigError,
    ProviderConfig,
    load_config,
    update_config_model,
)

FORBIDDEN_BUILTIN_NAMES = frozenset(
    {"jan", "xai", "mistral", "groq", "llamacpp", "vllm"}
)

EXPECTED_FIRST_SHIP_NAMES = (
    "openai",
    "anthropic",
    "gemini",
    "openrouter",
    "deepseek",
    "ollama",
    "lmstudio",
)


def test_parse_model_ref_multi_slash() -> None:
    """Multi-slash references split only on the first slash."""
    ref = parse_model_ref("openrouter/anthropic/claude-sonnet-4-5")
    assert ref == ModelRef(provider="openrouter", model="anthropic/claude-sonnet-4-5")
    assert str(ref) == "openrouter/anthropic/claude-sonnet-4-5"


def test_parse_model_ref_standard_pairs() -> None:
    """Standard provider/model references parse correctly."""
    assert parse_model_ref("openai/gpt-4o") == ModelRef("openai", "gpt-4o")
    assert parse_model_ref("anthropic/claude-3-5-sonnet") == ModelRef(
        "anthropic", "claude-3-5-sonnet"
    )


def test_parse_model_ref_bare_model_defaults_to_openai() -> None:
    """Bare model IDs without a slash default to provider 'openai'."""
    assert parse_model_ref("gpt-4o") == ModelRef("openai", "gpt-4o")
    assert parse_model_ref("o3-mini") == ModelRef("openai", "o3-mini")


def test_parse_model_ref_strips_whitespace() -> None:
    """Leading and trailing whitespace is stripped."""
    assert parse_model_ref("  deepseek/deepseek-chat  ") == ModelRef(
        "deepseek", "deepseek-chat"
    )


@pytest.mark.parametrize("invalid", ["", "   ", "/model", "model/"])
def test_parse_model_ref_invalid_format_raises_value_error(invalid: str) -> None:
    """Empty strings or missing components raise ValueError."""
    with pytest.raises(ValueError):
        parse_model_ref(invalid)


def test_load_config_unknown_provider_raises_config_error(
    aldwin_home: Path,
) -> None:
    """An unknown provider in config.yaml raises ConfigError listing known names."""
    atomic_write(aldwin_home / "config.yaml", "model: nope/x\n")
    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)

    message = str(exc_info.value)
    assert (
        "config.yaml: model: unknown provider 'nope' "
        "(known: anthropic, deepseek, gemini, lmstudio, ollama, openai, openrouter)"
    ) in message


def test_update_config_model_unknown_provider_raises_before_write(
    aldwin_home: Path,
) -> None:
    """update_config_model rejects unknown providers before modifying the file."""
    ensure_home(aldwin_home)
    config_path = aldwin_home / "config.yaml"
    initial_content = config_path.read_text(encoding="utf-8")

    with pytest.raises(ConfigError):
        update_config_model("unknown_prov/model", home=aldwin_home)

    assert config_path.read_text(encoding="utf-8") == initial_content


def test_unknown_provider_error_includes_active_custom_servers(
    aldwin_home: Path,
) -> None:
    """Custom providers in config.yaml are included in the known providers list."""
    atomic_write(
        aldwin_home / "config.yaml",
        "model: nope/x\n"
        "providers:\n"
        "  office-gpu:\n"
        "    shape: openai_chat\n"
        "    base_url: http://127.0.0.1:8000\n",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)

    message = str(exc_info.value)
    assert "office-gpu" in message
    assert "openai" in message


def test_load_builtin_providers_schema_and_entries() -> None:
    """Built-in catalogue loads exactly 7 providers with valid attributes."""
    providers = load_builtin_providers()
    assert len(providers) == 7
    names = tuple(p.name for p in providers)
    assert names == EXPECTED_FIRST_SHIP_NAMES

    for p in providers:
        assert isinstance(p, ProviderEntry)
        assert p.shape in {"openai_chat", "anthropic", "ollama"}
        assert p.base_url.startswith(("http://", "https://"))
        assert p.docs_url.startswith("https://")


def test_load_builtin_providers_is_cached() -> None:
    """load_builtin_providers is cached and returns the same tuple instance."""
    first = load_builtin_providers()
    second = load_builtin_providers()
    assert first is second


def test_builtin_catalogue_has_no_forbidden_names() -> None:
    """Jan, xAI, Mistral, Groq, llama.cpp, and vLLM must not be in built-ins."""
    providers = load_builtin_providers()
    names = {p.name for p in providers}
    forbidden_present = names & FORBIDDEN_BUILTIN_NAMES
    assert not forbidden_present, f"Forbidden built-ins present: {forbidden_present}"


def test_custom_provider_added_under_providers(aldwin_home: Path) -> None:
    """A new provider entry under providers: is parsed and listable."""
    atomic_write(
        aldwin_home / "config.yaml",
        "model: office-gpu/llama-3\n"
        "providers:\n"
        "  office-gpu:\n"
        "    shape: openai_chat\n"
        "    base_url: http://127.0.0.1:8000/v1\n",
    )
    cfg = load_config(aldwin_home)
    assert "office-gpu" in cfg.providers
    entry = get_provider("office-gpu", cfg)
    assert entry is not None
    assert entry.name == "office-gpu"
    assert entry.shape == "openai_chat"
    assert entry.base_url == "http://127.0.0.1:8000/v1"
    assert entry.local is False


def test_custom_provider_overrides_builtin_base_url(aldwin_home: Path) -> None:
    """Overriding a built-in provider updates its base_url while retaining shape."""
    atomic_write(
        aldwin_home / "config.yaml",
        "model: ollama/mistral\n"
        "providers:\n"
        "  ollama:\n"
        "    base_url: http://localhost:11435\n",
    )
    cfg = load_config(aldwin_home)
    entry = get_provider("ollama", cfg)
    assert entry is not None
    assert entry.base_url == "http://localhost:11435"
    assert entry.shape == "ollama"
    assert entry.local is True


def test_custom_provider_overrides_builtin_secret_and_base_url(
    aldwin_home: Path,
) -> None:
    """Overriding openai allows custom proxy URL and custom secret key."""
    atomic_write(
        aldwin_home / "config.yaml",
        "providers:\n"
        "  openai:\n"
        "    base_url: https://proxy.internal/v1\n"
        "    secret: custom_openai_key\n",
    )
    cfg = load_config(aldwin_home)
    entry = get_provider("openai", cfg)
    assert entry is not None
    assert entry.base_url == "https://proxy.internal/v1"
    assert entry.secret == "custom_openai_key"
    assert entry.shape == "openai_chat"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("[::1]", True),
        ("127.0.1.1", True),
        ("192.168.1.50", False),
        ("10.0.0.1", False),
        ("8.8.8.8", False),
        ("api.openai.com", False),
        ("", False),
    ],
)
def test_is_loopback_host(host: str, expected: bool) -> None:
    """is_loopback_host identifies loopback IPs and hostnames accurately."""
    assert is_loopback_host(host) is expected


def test_insecure_remote_http_without_local_true_rejected(
    aldwin_home: Path,
) -> None:
    """Non-loopback http:// without local: true raises ConfigError on base_url."""
    atomic_write(
        aldwin_home / "config.yaml",
        "providers:\n"
        "  office-gpu:\n"
        "    shape: openai_chat\n"
        "    base_url: http://192.168.1.50:8000/v1\n",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)

    message = str(exc_info.value)
    assert "providers.office-gpu.base_url" in message
    assert "non-loopback http:// requires local: true" in message


def test_insecure_remote_http_with_local_true_accepted(
    aldwin_home: Path,
) -> None:
    """Non-loopback http:// with local: true is accepted for on-prem servers."""
    atomic_write(
        aldwin_home / "config.yaml",
        "providers:\n"
        "  office-gpu:\n"
        "    shape: openai_chat\n"
        "    base_url: http://192.168.1.50:8000/v1\n"
        "    local: true\n",
    )
    cfg = load_config(aldwin_home)
    assert cfg.providers["office-gpu"].local is True
    entry = get_provider("office-gpu", cfg)
    assert entry is not None
    assert entry.local is True


def test_loopback_http_without_local_flag_accepted(aldwin_home: Path) -> None:
    """Loopback addresses with http:// are permitted without local: true."""
    atomic_write(
        aldwin_home / "config.yaml",
        "providers:\n"
        "  local-dev:\n"
        "    shape: openai_chat\n"
        "    base_url: http://localhost:1234/v1\n",
    )
    cfg = load_config(aldwin_home)
    assert "local-dev" in cfg.providers


def test_invalid_provider_shape_raises_config_error(aldwin_home: Path) -> None:
    """An unsupported provider shape raises ConfigError naming valid choices."""
    atomic_write(
        aldwin_home / "config.yaml",
        "providers:\n"
        "  bad:\n"
        "    shape: proprietary\n"
        "    base_url: https://api.example.com\n",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)

    message = str(exc_info.value)
    assert "providers.bad.shape" in message
    assert "'openai_chat', 'anthropic', or 'ollama'" in message


def test_invalid_url_scheme_raises_config_error(aldwin_home: Path) -> None:
    """Non-http(s) scheme raises ConfigError on base_url."""
    atomic_write(
        aldwin_home / "config.yaml",
        "providers:\n"
        "  bad:\n"
        "    shape: openai_chat\n"
        "    base_url: ftp://example.com/v1\n",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)

    message = str(exc_info.value)
    assert "providers.bad.base_url" in message


def test_list_providers_ordering() -> None:
    """list_providers returns built-ins first, then custom providers sorted."""
    cfg = AldwinConfig(
        providers={
            "zeta-server": ProviderConfig(
                shape="openai_chat", base_url="https://zeta.internal"
            ),
            "alpha-server": ProviderConfig(
                shape="openai_chat", base_url="https://alpha.internal"
            ),
        }
    )
    providers = list_providers(cfg)
    names = [p.name for p in providers]

    # First 7 are the built-ins in display order
    assert tuple(names[:7]) == EXPECTED_FIRST_SHIP_NAMES
    # Following are customs sorted alphabetically
    assert names[7:] == ["alpha-server", "zeta-server"]


def test_get_provider_unknown_returns_none() -> None:
    """get_provider returns None when provider name is not found."""
    assert get_provider("nonexistent") is None


def test_validate_model_ref_valid_and_unknown() -> None:
    """validate_model_ref returns ModelRef on success and ConfigError on unknown."""
    ref = validate_model_ref("openai/gpt-4o")
    assert ref == ModelRef("openai", "gpt-4o")

    with pytest.raises(ConfigError) as exc_info:
        validate_model_ref("unknown/model")

    assert "unknown provider 'unknown'" in str(exc_info.value)

    with pytest.raises(ConfigError) as exc_invalid:
        validate_model_ref("")
    assert "must be a non-empty string" in str(exc_invalid.value)


def test_list_providers_custom_without_shape_or_base_url_raises_config_error() -> None:
    cfg_no_shape = AldwinConfig(
        providers={"no-shape-custom": ProviderConfig(base_url="https://example.com")}
    )
    with pytest.raises(ConfigError) as exc_shape:
        list_providers(cfg_no_shape)
    assert exc_shape.value.field == "providers.no-shape-custom.shape"
    assert "'openai_chat', 'anthropic', or 'ollama'" in str(exc_shape.value)

    cfg_no_url = AldwinConfig(
        providers={"no-url-custom": ProviderConfig(shape="openai_chat")}
    )
    with pytest.raises(ConfigError) as exc_url:
        list_providers(cfg_no_url)
    assert exc_url.value.field == "providers.no-url-custom.base_url"


def test_custom_provider_case_insensitive_override_and_secret_only(
    aldwin_home: Path,
) -> None:
    """Upper-cased provider keys override built-ins and secret-only overrides work."""
    atomic_write(
        aldwin_home / "config.yaml",
        "providers:\n  OpenAI:\n    secret: custom_openai_key\n",
    )
    cfg = load_config(aldwin_home)
    entry = get_provider("openai", cfg)
    assert entry is not None
    assert entry.secret == "custom_openai_key"
    assert entry.base_url == "https://api.openai.com/v1"
    assert entry.shape == "openai_chat"


def test_load_builtin_providers_duplicate_names_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate provider names in providers.yaml raise ValueError."""
    import aldwin.core.providers.registry as reg_mod

    dup_yaml = (
        "providers:\n"
        "  - name: p1\n"
        "    title: P1\n"
        "    shape: openai_chat\n"
        "    base_url: https://a\n"
        "  - name: p1\n"
        "    title: P1 Dup\n"
        "    shape: openai_chat\n"
        "    base_url: https://b\n"
    )
    reg_mod.load_builtin_providers.cache_clear()

    class FakeAsset:
        def read_text(self, encoding: str = "utf-8") -> str:
            return dup_yaml

    fake_asset = FakeAsset()
    monkeypatch.setattr(
        reg_mod,
        "files",
        lambda pkg: type("F", (), {"joinpath": lambda self, name: fake_asset})(),
    )
    with pytest.raises(ValueError, match="duplicate provider names"):
        reg_mod.load_builtin_providers()
    reg_mod.load_builtin_providers.cache_clear()
