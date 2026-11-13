"""Unit tests for aldwin.core.settings."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Iterator
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

from aldwin.core.home import atomic_write, ensure_home
from aldwin.core.settings import (
    AldwinConfig,
    ConfigError,
    LimitsConfig,
    ResolvedKey,
    SearchConfig,
    SecretsConfig,
    config_locked,
    config_secrets_fingerprint,
    load_config,
    load_secrets,
    mask_secret,
    resolve_key,
    save_secrets,
    secrets_locked,
    update_config_check_for_updates,
    update_config_last_seen_version,
    update_config_limits,
    update_config_model,
    update_config_search_provider,
    update_config_theme,
)


def test_load_config_missing_file_returns_defaults(aldwin_home: Path) -> None:
    """Missing config.yaml returns all default settings without error."""
    cfg = load_config(aldwin_home)
    assert cfg == AldwinConfig(
        model="gpt-4o",
        theme="dark",
        limits=LimitsConfig(
            context_exchanges=20,
            profile_chars=4000,
            notes_chars=4000,
            notes_days=7,
            persona_chars=2000,
            max_steps=15,
            web_searches=5,
            connector_tools=40,
        ),
    )


def test_starter_assets_are_valid(aldwin_home: Path) -> None:
    """Starter config.yaml and packaged default skills load cleanly."""
    from aldwin.core.skills import build_catalogue

    ensure_home(aldwin_home)
    cfg = load_config(aldwin_home)
    assert cfg == AldwinConfig()

    cat = build_catalogue(aldwin_home, version="0.0.2", model=cfg.model)
    ua = next(e for e in cat if e.name == "using-aldwin")
    assert ua.skill is not None
    assert "Aldwin" in ua.body
    assert not (aldwin_home / "skills" / "using-aldwin").exists()

    profile_raw = (aldwin_home / "memory" / "profile.md").read_text(encoding="utf-8")
    stripped_profile = re.sub(r"<!--.*?-->", "", profile_raw, flags=re.DOTALL).strip()
    assert stripped_profile == ""


def test_load_config_empty_or_comments_only(aldwin_home: Path) -> None:
    """Empty or comment-only config.yaml returns defaults."""
    config_path = aldwin_home / "config.yaml"
    atomic_write(config_path, "# only a comment\n")

    assert load_config(aldwin_home) == AldwinConfig()


def test_load_config_invalid_value_raises_config_error(aldwin_home: Path) -> None:
    """Invalid limits.max_steps names file, field, and expected type."""
    config_path = aldwin_home / "config.yaml"
    atomic_write(config_path, 'limits:\n  max_steps: "lots"\n')

    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)

    message = str(exc_info.value)
    assert str(config_path) in message
    assert "limits.max_steps" in message
    assert "integer >= 1" in message
    assert "restore defaults" in message


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("check_for_updates: maybe\n", "'check_for_updates' must be true or false."),
        (
            "last_seen_version: [1]\n",
            "'last_seen_version' must be a version string such as '0.0.2'.",
        ),
        (
            "run_command:\n  env_allow: [1]\n",
            "'run_command.env_allow[0]' must be an environment variable name "
            "(a string).",
        ),
    ],
)
def test_load_config_error_reads_as_a_sentence(
    aldwin_home: Path, body: str, expected: str
) -> None:
    """Every config field gets a "must be ..." phrase, not pydantic's raw text."""
    atomic_write(aldwin_home / "config.yaml", body)

    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)

    message = str(exc_info.value)
    assert expected in message
    assert "Input should be" not in message


def test_load_config_error_for_unmapped_field_keeps_detail(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A field missing from the expected-types map still yields a readable error."""
    import aldwin.core.settings as settings_mod

    trimmed = dict(settings_mod._EXPECTED_FIELD_TYPES)
    del trimmed["check_for_updates"]
    monkeypatch.setattr(settings_mod, "_EXPECTED_FIELD_TYPES", trimmed)
    atomic_write(aldwin_home / "config.yaml", "check_for_updates: maybe\n")

    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)

    message = str(exc_info.value)
    assert "'check_for_updates' must be a valid value (Input should be" in message
    assert "must be Input should be" not in message


def test_load_config_unknown_keys_logged_and_ignored(
    aldwin_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unknown keys in config.yaml are ignored and logged as warnings."""
    config_path = aldwin_home / "config.yaml"
    atomic_write(
        config_path,
        "model: gpt-4o\nfuture_flag: true\nlimits:\n  unknown_limit: 99\n",
    )

    with caplog.at_level(logging.WARNING, logger="aldwin.core.settings"):
        cfg = load_config(aldwin_home)

    assert cfg.model == "gpt-4o"
    assert "future_flag" in caplog.text
    assert "limits.unknown_limit" in caplog.text


def test_load_config_malformed_yaml_or_non_dict_raises_config_error(
    aldwin_home: Path,
) -> None:
    """Corrupted YAML or non-mapping root raises ConfigError."""
    config_path = aldwin_home / "config.yaml"

    atomic_write(config_path, "model: [unclosed\n")
    with pytest.raises(ConfigError) as exc_yaml:
        load_config(aldwin_home)
    assert str(config_path) in str(exc_yaml.value)
    assert "valid YAML syntax" in str(exc_yaml.value)

    atomic_write(config_path, "- item1\n- item2\n")
    with pytest.raises(ConfigError) as exc_root:
        load_config(aldwin_home)
    assert str(config_path) in str(exc_root.value)
    assert "<root>" in str(exc_root.value)


@pytest.mark.parametrize(
    ("yaml_text", "expected_field", "expected_type"),
    [
        ("limits:\n  max_steps: 0\n", "limits.max_steps", "integer >= 1"),
        ("limits:\n  profile_chars: 99\n", "limits.profile_chars", "integer >= 100"),
        ("limits:\n  connector_tools: 4\n", "limits.connector_tools", "integer >= 5"),
        (
            "theme: neon\n",
            "theme",
            "'auto', 'dark', 'light', 'colour_blind', or 'plain'",
        ),
        ("limits: true\n", "limits", "mapping of limit settings"),
        ('model: "   "\n', "model", "non-empty string"),
    ],
)
def test_load_config_rejects_out_of_range_limits_and_bad_theme(
    aldwin_home: Path,
    yaml_text: str,
    expected_field: str,
    expected_type: str,
) -> None:
    """Boundary validation on LimitsConfig, model, and theme."""
    config_path = aldwin_home / "config.yaml"
    atomic_write(config_path, yaml_text)

    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)

    message = str(exc_info.value)
    assert expected_field in message
    assert expected_type in message


def test_load_config_empty_limits_section_uses_defaults(aldwin_home: Path) -> None:
    """An empty `limits:` key in config.yaml falls back to default LimitsConfig."""
    config_path = aldwin_home / "config.yaml"
    atomic_write(config_path, "model: gpt-4o\nlimits:\n  # all limits commented out\n")

    cfg = load_config(aldwin_home)
    assert cfg.limits == LimitsConfig()


def test_update_config_model_preserves_comments(aldwin_home: Path) -> None:
    """Update_config_model updates model: while preserving comments and 0600."""
    ensure_home(aldwin_home)
    config_path = aldwin_home / "config.yaml"

    update_config_model("gpt-4.1", aldwin_home)

    raw_text = config_path.read_text(encoding="utf-8")
    assert "# Default OpenAI chat model" in raw_text
    assert "# Context and turn limits" in raw_text
    assert (config_path.stat().st_mode & 0o777) == 0o600
    assert load_config(aldwin_home).model == "gpt-4.1"


def test_update_config_search_provider_preserves_comments(aldwin_home: Path) -> None:
    """update_config_search_provider writes search.provider without wiping comments."""
    ensure_home(aldwin_home)
    config_path = aldwin_home / "config.yaml"

    update_config_search_provider("searxng", aldwin_home)

    raw_text = config_path.read_text(encoding="utf-8")
    assert "# Default OpenAI chat model" in raw_text
    assert "search:" in raw_text
    assert "provider: searxng" in raw_text
    assert load_config(aldwin_home).search.provider == "searxng"

    update_config_search_provider("auto", aldwin_home)
    assert load_config(aldwin_home).search.provider == "auto"
    assert "# Default OpenAI chat model" in config_path.read_text(encoding="utf-8")


def test_resolve_key_env_takes_priority_over_file(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OPENAI_API_KEY in environment overrides secrets.yaml."""
    save_secrets(
        SecretsConfig(openai_api_key=SecretStr("sk-file-key-1111")),
        aldwin_home,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key-2222")

    resolved = resolve_key("openai", aldwin_home)
    assert resolved.key == "sk-env-key-2222"
    assert resolved.source == "env"


def test_save_and_load_secrets_permissions_and_masking(aldwin_home: Path) -> None:
    """Secrets saved with mode 0600, valid YAML, and masked str/repr."""
    secrets = SecretsConfig(
        openai_api_key=SecretStr("sk-proj-12345678a1b2"),
        tavily_api_key=SecretStr("tvly-dev-98765432z9y8"),
    )
    secrets_path = save_secrets(secrets, aldwin_home)

    assert (secrets_path.stat().st_mode & 0o777) == 0o600
    raw_yaml = yaml.safe_load(secrets_path.read_text(encoding="utf-8"))
    assert raw_yaml == {
        "openai_api_key": "sk-proj-12345678a1b2",
        "tavily_api_key": "tvly-dev-98765432z9y8",
    }

    for text_form in (str(secrets), repr(secrets)):
        assert "sk-…a1b2" in text_form
        assert "tvly-…z9y8" in text_form
        assert "12345678" not in text_form
        assert "98765432" not in text_form

    loaded = load_secrets(aldwin_home)
    assert loaded.openai_api_key is not None
    assert loaded.openai_api_key.get_secret_value() == "sk-proj-12345678a1b2"


def test_load_secrets_repairs_loose_file_permissions(
    aldwin_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Secrets.yaml found with mode 0644 is repaired to 0600 with warning."""
    secrets_path = save_secrets(
        SecretsConfig(openai_api_key=SecretStr("sk-proj-12345678a1b2")),
        aldwin_home,
    )
    os.chmod(secrets_path, 0o644)

    with caplog.at_level(logging.WARNING, logger="aldwin.core.home"):
        loaded = load_secrets(aldwin_home)

    assert loaded.openai_api_key is not None
    assert (secrets_path.stat().st_mode & 0o777) == 0o600
    assert "0644" in caplog.text


def test_resolve_key_file_and_missing_sources(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank env var falls back to secrets.yaml; absent key returns missing."""
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    save_secrets(
        SecretsConfig(openai_api_key=SecretStr("sk-file-key-9999")),
        aldwin_home,
    )

    openai_key = resolve_key("openai", aldwin_home)
    assert openai_key.key == "sk-file-key-9999"
    assert openai_key.source == "file"

    tavily_key = resolve_key("tavily", aldwin_home)
    assert tavily_key.key is None
    assert tavily_key.source == "missing"


def test_mask_secret_and_resolved_key_repr() -> None:
    """Mask_secret and ResolvedKey never leak short or full keys."""
    assert mask_secret(None) == ""
    assert mask_secret("") == ""
    assert mask_secret("   ") == ""
    assert mask_secret("short") == "***"
    assert mask_secret("sk-12345678abcd") == "sk-…abcd"
    assert mask_secret("tvly-12345678wxyz") == "tvly-…wxyz"
    assert mask_secret("customkey123456") == "cus…3456"

    resolved = ResolvedKey(key="sk-12345678abcd", source="env")
    assert "sk-…abcd" in repr(resolved)
    assert "12345678" not in repr(resolved)
    assert "12345678" not in str(resolved)


def test_update_config_theme_and_last_seen_version(aldwin_home: Path) -> None:
    """Theme and last_seen_version persist to config.yaml with validation."""
    ensure_home(aldwin_home)
    update_config_theme("light", aldwin_home)
    cfg = load_config(aldwin_home)
    assert cfg.theme == "light"

    update_config_theme("colour_blind", aldwin_home)
    assert load_config(aldwin_home).theme == "colour_blind"

    with pytest.raises(ConfigError) as exc_info:
        update_config_theme("neon", aldwin_home)
    assert exc_info.value.field == "theme"

    update_config_last_seen_version("0.0.2", aldwin_home)
    assert load_config(aldwin_home).last_seen_version == "0.0.2"

    with pytest.raises(ConfigError) as exc_info2:
        update_config_last_seen_version("   ", aldwin_home)
    assert exc_info2.value.field == "last_seen_version"


def test_update_config_check_for_updates_keeps_comments(aldwin_home: Path) -> None:
    """The update-check switch is written as a YAML boolean, under the lock."""
    ensure_home(aldwin_home)
    config_path = aldwin_home / "config.yaml"
    config_path.write_text(
        "# my notes\nmodel: gpt-4o\ncheck_for_updates: true  # keep\n",
        encoding="utf-8",
    )
    update_config_check_for_updates(False, aldwin_home)
    text = config_path.read_text("utf-8")
    assert "# my notes" in text
    assert "check_for_updates: false" in text
    assert load_config(aldwin_home).check_for_updates is False

    update_config_check_for_updates(True, aldwin_home)
    assert load_config(aldwin_home).check_for_updates is True

    with pytest.raises(ConfigError) as exc_info:
        update_config_check_for_updates("no", aldwin_home)  # type: ignore[arg-type]
    assert exc_info.value.field == "check_for_updates"


def test_update_config_check_for_updates_waits_for_the_config_lock(
    aldwin_home: Path,
) -> None:
    """Another writer holding the config lock delays this write until it is done."""
    import threading

    ensure_home(aldwin_home)
    done = threading.Event()
    with config_locked(aldwin_home):

        def write() -> None:
            update_config_check_for_updates(False, aldwin_home)
            done.set()

        worker = threading.Thread(target=write)
        worker.start()
        assert not done.wait(0.2)
    worker.join(timeout=5)
    assert done.is_set()
    assert load_config(aldwin_home).check_for_updates is False


def test_extensible_provider_secrets_preserved_and_resolved(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extra provider keys (e.g. anthropic, brave) are kept, masked, and resolved."""
    secrets_path = aldwin_home / "secrets.yaml"
    atomic_write(
        secrets_path,
        "anthropic_api_key: sk-ant-12345678wxyz\nbrave_api_key: bsa-12345678abcd\n",
    )

    save_secrets(
        SecretsConfig(openai_api_key=SecretStr("sk-proj-12345678a1b2")),
        aldwin_home,
    )

    loaded = load_secrets(aldwin_home)
    assert loaded.get_secret("openai") == "sk-proj-12345678a1b2"
    assert loaded.get_secret("anthropic") == "sk-ant-12345678wxyz"
    assert loaded.get_secret("brave") == "bsa-12345678abcd"
    assert "sk-…wxyz" in repr(loaded)
    assert "bsa…abcd" in repr(loaded)
    assert "12345678" not in repr(loaded)

    file_resolved = resolve_key("anthropic", aldwin_home)
    assert file_resolved.key == "sk-ant-12345678wxyz"
    assert file_resolved.source == "file"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-99998888")
    env_resolved = resolve_key("anthropic", aldwin_home)
    assert env_resolved.key == "sk-ant-env-99998888"
    assert env_resolved.source == "env"


def test_connectors_import_paths_must_be_a_list(aldwin_home: Path) -> None:
    (aldwin_home / "config.yaml").write_text(
        "model: gpt-4o\nconnectors:\n  import_paths: x\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as info:
        load_config(aldwin_home)
    assert "'connectors.import_paths' must be a list of paths" in str(info.value)

    (aldwin_home / "config.yaml").write_text(
        "model: gpt-4o\nconnectors:\n  import_paths: [~/team/mcp.toml]\n",
        encoding="utf-8",
    )
    assert load_config(aldwin_home).connectors.import_paths == ["~/team/mcp.toml"]


def test_search_config_defaults(aldwin_home: Path) -> None:
    """Default search config is auto with no SearXNG URL."""
    cfg = load_config(aldwin_home)
    assert cfg.search == SearchConfig(
        provider="auto",
        searxng_url=None,
    )


def test_search_config_valid_nested_yaml(aldwin_home: Path) -> None:
    """Nested search YAML loads provider and SearXNG URL."""
    atomic_write(
        aldwin_home / "config.yaml",
        (
            "model: gpt-4o\n"
            "search:\n"
            "  provider: searxng\n"
            "  searxng_url: http://127.0.0.1:8080\n"
        ),
    )
    cfg = load_config(aldwin_home)
    assert cfg.search.provider == "searxng"
    assert cfg.search.searxng_url == "http://127.0.0.1:8080"


@pytest.mark.parametrize("provider", ["bing", "duckduckgo"])
def test_search_config_invalid_provider(aldwin_home: Path, provider: str) -> None:
    """Unknown or removed search.provider raises ConfigError naming the field.

    Covers a never-shipped name (bing) and the removed DuckDuckGo catalogue
    entry so a stale ``provider: duckduckgo`` fails closed.
    """
    atomic_write(
        aldwin_home / "config.yaml",
        f"model: gpt-4o\nsearch:\n  provider: {provider}\n",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(aldwin_home)
    message = str(exc_info.value)
    assert "search.provider" in message
    assert "must be" in message


def test_load_config_providers_validation_and_unknown_warnings(
    aldwin_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Custom providers in config.yaml validate shape, base_url, and warn on typos."""
    config_path = aldwin_home / "config.yaml"

    # Empty providers: section defaults to empty dict
    atomic_write(config_path, "model: gpt-4o\nproviders:\n")
    assert load_config(aldwin_home).providers == {}

    # Missing shape on new custom provider raises ConfigError on providers.<name>.shape
    atomic_write(
        config_path,
        "providers:\n  office-gpu:\n    base_url: https://gpu.internal/v1\n",
    )
    with pytest.raises(ConfigError) as exc_shape:
        load_config(aldwin_home)
    assert exc_shape.value.file == config_path
    assert exc_shape.value.field == "providers.office-gpu.shape"
    assert (
        "'providers.office-gpu.shape' must be 'openai_chat', 'anthropic', or 'ollama'"
        in str(exc_shape.value)
    )

    # Missing base_url on new custom provider raises ConfigError on base_url
    atomic_write(
        config_path,
        "providers:\n  office-gpu:\n    shape: openai_chat\n",
    )
    with pytest.raises(ConfigError) as exc_url:
        load_config(aldwin_home)
    assert exc_url.value.file == config_path
    assert exc_url.value.field == "providers.office-gpu.base_url"

    # Duplicate casefolded keys in providers: raise ConfigError
    atomic_write(
        config_path,
        "providers:\n"
        "  OpenAI:\n"
        "    base_url: https://proxy1.internal/v1\n"
        "  openai:\n"
        "    base_url: https://proxy2.internal/v1\n",
    )
    with pytest.raises(ConfigError) as exc_dup:
        load_config(aldwin_home)
    assert "providers" in exc_dup.value.field

    # Unknown keys inside a provider block log a warning and load cleanly
    atomic_write(
        config_path,
        "providers:\n"
        "  office-gpu:\n"
        "    shape: openai_chat\n"
        "    base_url: https://gpu.internal/v1\n"
        "    extra_typo: 123\n",
    )
    with caplog.at_level(logging.WARNING, logger="aldwin.core.settings"):
        cfg = load_config(aldwin_home)
    assert "office-gpu" in cfg.providers
    assert "Ignoring unknown key 'providers.office-gpu.extra_typo'" in caplog.text


def test_first_ship_cloud_secrets_typed_and_masked() -> None:
    """First-ship provider keys are typed SecretStr and masked in str and repr."""
    secrets = SecretsConfig(
        anthropic_api_key=SecretStr("sk-ant-api03-12345678abcd"),
        gemini_api_key=SecretStr("AIzaSy1234567890abcdefghijklmnopqrstuvw"),
        openrouter_api_key=SecretStr("sk-or-v1-1234567890abcdef"),
        deepseek_api_key=SecretStr("sk-1234567890abcdef1234567890abcdef"),
    )
    assert secrets.get_secret("anthropic") == "sk-ant-api03-12345678abcd"
    assert secrets.get_secret("gemini") == "AIzaSy1234567890abcdefghijklmnopqrstuvw"
    assert secrets.get_secret("openrouter") == "sk-or-v1-1234567890abcdef"
    assert secrets.get_secret("deepseek") == "sk-1234567890abcdef1234567890abcdef"

    as_dict = secrets.to_secret_dict()
    assert as_dict["anthropic_api_key"] == "sk-ant-api03-12345678abcd"
    assert as_dict["gemini_api_key"] == "AIzaSy1234567890abcdefghijklmnopqrstuvw"
    assert as_dict["openrouter_api_key"] == "sk-or-v1-1234567890abcdef"
    assert as_dict["deepseek_api_key"] == "sk-1234567890abcdef1234567890abcdef"

    text_str = str(secrets)
    text_repr = repr(secrets)
    for form in (text_str, text_repr):
        assert "sk-…abcd" in form
        assert "12345678" not in form
        assert "AIzaSy12" not in form


def test_resolve_key_custom_secret_field(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resolve_key checks uppercase env of custom secret_field before file."""
    monkeypatch.setenv("MY_OFFICE_TOKEN", "env-office-secret-123")
    save_secrets(
        SecretsConfig(my_office_token=SecretStr("file-office-secret-456")),
        aldwin_home,
    )

    resolved_env = resolve_key(
        "office-gpu", aldwin_home, secret_field="my_office_token"
    )
    assert resolved_env.key == "env-office-secret-123"
    assert resolved_env.source == "env"

    monkeypatch.delenv("MY_OFFICE_TOKEN", raising=False)
    resolved_file = resolve_key(
        "office-gpu", aldwin_home, secret_field="my_office_token"
    )
    assert resolved_file.key == "file-office-secret-456"
    assert resolved_file.source == "file"


async def test_concurrent_save_secrets_maintains_yaml_integrity(
    aldwin_home: Path,
) -> None:
    """Concurrent save_secrets calls hold lock and persist all keys in valid YAML."""
    import asyncio

    async def _worker(idx: int) -> None:
        cfg = SecretsConfig(**{f"key_{idx}": SecretStr(f"secret_{idx}")})
        await asyncio.to_thread(save_secrets, cfg, aldwin_home)

    await asyncio.gather(*[_worker(i) for i in range(10)])

    secrets_path = aldwin_home / "secrets.yaml"
    assert secrets_path.exists()
    assert (secrets_path.stat().st_mode & 0o777) == 0o600

    raw = yaml.safe_load(secrets_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert len(raw) == 10
    for idx in range(10):
        assert raw.get(f"key_{idx}") == f"secret_{idx}"


def _slow_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen the gap between read and write so a missing lock loses updates."""
    import time

    import aldwin.core.settings as settings_module

    real = settings_module._read_config_source

    def slow(path: Path) -> str:
        text = real(path)
        time.sleep(0.02)
        return text

    monkeypatch.setattr(settings_module, "_read_config_source", slow)


async def test_concurrent_update_config_maintains_yaml_integrity(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent writers serialise, so no update is lost or torn."""
    import asyncio

    _slow_reads(monkeypatch)
    wanted = {
        "context_exchanges": 31,
        "profile_chars": 4100,
        "notes_chars": 4200,
        "notes_days": 9,
        "persona_chars": 2100,
        "max_steps": 21,
        "web_searches": 6,
        "connector_tools": 44,
    }
    jobs: list[Awaitable[object]] = [
        asyncio.to_thread(update_config_limits, {key: value}, aldwin_home)
        for key, value in wanted.items()
    ]
    jobs.append(asyncio.to_thread(update_config_theme, "light", aldwin_home))
    jobs.append(
        asyncio.to_thread(update_config_last_seen_version, "9.9.9", aldwin_home)
    )

    await asyncio.gather(*jobs)

    cfg = load_config(aldwin_home)
    assert cfg.limits.model_dump() == wanted
    assert cfg.theme == "light"
    assert cfg.last_seen_version == "9.9.9"
    config_path = aldwin_home / "config.yaml"
    assert (config_path.stat().st_mode & 0o777) == 0o600


def test_nested_config_then_secrets_lock_order_no_deadlock(aldwin_home: Path) -> None:
    """Two writers nesting config then secrets both finish."""
    import threading

    finished: list[int] = []

    def nested(idx: int) -> None:
        for _ in range(20):
            with config_locked(aldwin_home), secrets_locked(aldwin_home):
                pass
        finished.append(idx)

    threads = [threading.Thread(target=nested, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(finished) == [0, 1]


def test_no_production_helper_nests_secrets_before_config() -> None:
    """No code takes the config lock while holding the secrets lock."""
    import ast

    src = Path(__file__).resolve().parent.parent / "src" / "aldwin"
    import aldwin.core.settings as settings_module

    config_writers = {"config_locked"} | {
        name
        for name in dir(settings_module)
        if name.startswith("update_config_") or name == "ensure_run_command_env_allow"
    }
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            holds_secrets = any(
                isinstance(item.context_expr, ast.Call)
                and getattr(item.context_expr.func, "id", "") == "secrets_locked"
                for item in node.items
            )
            if not holds_secrets:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    name = getattr(inner.func, "id", getattr(inner.func, "attr", ""))
                    if name in config_writers:
                        offenders.append(f"{path.name}:{inner.lineno}")
    assert offenders == []


def test_update_config_limits_single_rewrite_multi_key(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Several limits change in one locked write."""
    import aldwin.core.settings as settings_module

    writes: list[Path] = []
    real = settings_module.atomic_write

    def counting(path: Path, data: str, mode: int) -> None:
        writes.append(path)
        real(path, data, mode=mode)

    monkeypatch.setattr(settings_module, "atomic_write", counting)

    result = update_config_limits(
        {"max_steps": 30, "web_searches": 8, "notes_days": 3}, aldwin_home
    )

    assert writes == [aldwin_home / "config.yaml"]
    assert (result.max_steps, result.web_searches, result.notes_days) == (30, 8, 3)
    assert load_config(aldwin_home).limits == result


def test_config_locked_creates_lock_file_mode_0600(tmp_path: Path) -> None:
    """The lock lives beside config.yaml and only the owner can read it."""
    home = tmp_path / "home"
    with config_locked(home):
        lock = home / "config.yaml.lock"
        assert lock.exists()
    assert (lock.stat().st_mode & 0o777) == 0o600


def test_config_secrets_fingerprint_changes_on_config(aldwin_home: Path) -> None:
    """Rewriting config.yaml changes the fingerprint."""
    before = config_secrets_fingerprint(aldwin_home)
    update_config_limits({"max_steps": 99}, aldwin_home)
    assert config_secrets_fingerprint(aldwin_home)[:2] != before[:2]


def test_config_secrets_fingerprint_changes_on_secrets(aldwin_home: Path) -> None:
    """Saving secrets.yaml changes the fingerprint."""
    before = config_secrets_fingerprint(aldwin_home)
    save_secrets(SecretsConfig(openai_api_key=SecretStr("sk-new-value")), aldwin_home)
    after = config_secrets_fingerprint(aldwin_home)
    assert after[2:] != before[2:]
    assert after[:2] == before[:2]


def test_config_secrets_fingerprint_ignores_lock_files(aldwin_home: Path) -> None:
    """Taking or touching the lock files is not a settings change."""
    before = config_secrets_fingerprint(aldwin_home)
    with config_locked(aldwin_home), secrets_locked(aldwin_home):
        (aldwin_home / "config.yaml.lock").write_text("x", encoding="utf-8")
        (aldwin_home / "secrets.yaml.lock").write_text("x", encoding="utf-8")
    assert config_secrets_fingerprint(aldwin_home) == before


def test_config_secrets_fingerprint_missing_files_are_zeros(tmp_path: Path) -> None:
    """A missing file counts as zeros for its pair."""
    assert config_secrets_fingerprint(tmp_path) == (0, 0, 0, 0)
    (tmp_path / "config.yaml").write_text("model: gpt-4o\n", encoding="utf-8")
    fingerprint = config_secrets_fingerprint(tmp_path)
    assert fingerprint[0] > 0 and fingerprint[1] > 0
    assert fingerprint[2:] == (0, 0)


def test_config_secrets_fingerprint_tuple_shape(aldwin_home: Path) -> None:
    """Four integers: config mtime and size, then secrets."""
    fingerprint = config_secrets_fingerprint(aldwin_home)
    assert len(fingerprint) == 4
    assert all(isinstance(part, int) for part in fingerprint)


def test_update_config_writers_use_config_locked(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every config.yaml writer takes the config lock."""
    from contextlib import contextmanager

    import aldwin.core.settings as settings_module
    from aldwin.core.settings import (
        ProviderConfig,
        ensure_run_command_env_allow,
        update_config_provider,
        update_config_searxng_url,
    )

    taken: list[Path] = []
    real = settings_module.config_locked

    @contextmanager
    def recording(home: Path) -> Iterator[None]:
        taken.append(home)
        with real(home):
            yield

    monkeypatch.setattr(settings_module, "config_locked", recording)
    writers = [
        lambda: update_config_model("gpt-4o-mini", aldwin_home),
        lambda: update_config_theme("light", aldwin_home),
        lambda: update_config_last_seen_version("1.2.3", aldwin_home),
        lambda: update_config_search_provider("auto", aldwin_home),
        lambda: update_config_searxng_url("https://search.example", aldwin_home),
        lambda: update_config_limits({"max_steps": 12}, aldwin_home),
        lambda: ensure_run_command_env_allow(aldwin_home),
        lambda: update_config_provider(
            "office",
            ProviderConfig(
                shape="openai_chat", base_url="http://127.0.0.1:8000/v1", local=True
            ),
            aldwin_home,
        ),
    ]
    for index, write in enumerate(writers, start=1):
        write()
        assert len(taken) == index, f"writer {index} did not take the lock"


def test_update_config_limits_partial_merge(aldwin_home: Path) -> None:
    """Keys not in the update keep their values."""
    update_config_limits({"notes_days": 4}, aldwin_home)
    update_config_limits({"max_steps": 25}, aldwin_home)

    limits = load_config(aldwin_home).limits
    assert limits.notes_days == 4
    assert limits.max_steps == 25
    assert limits.context_exchanges == LimitsConfig().context_exchanges


def test_update_config_limits_empty_dict_noop(aldwin_home: Path) -> None:
    """An empty update leaves the file and limits unchanged."""
    config_path = aldwin_home / "config.yaml"
    before = config_path.read_text(encoding="utf-8")

    fingerprint = config_secrets_fingerprint(aldwin_home)

    result = update_config_limits({}, aldwin_home)
    update_config_limits({"max_steps": result.max_steps}, aldwin_home)

    assert config_path.read_text(encoding="utf-8") == before
    assert config_secrets_fingerprint(aldwin_home) == fingerprint
    assert result == load_config(aldwin_home).limits


@pytest.mark.parametrize(
    ("updates", "field"),
    [
        ({"max_steps": 0}, "limits.max_steps"),
        ({"notes_days": "seven"}, "limits.notes_days"),
        ({"connector_tools": 2}, "limits.connector_tools"),
        ({"max_stepz": 5}, "limits.max_stepz"),
    ],
)
def test_update_config_limits_invalid_raises_config_error(
    aldwin_home: Path, updates: dict[str, object], field: str
) -> None:
    """A bad or unknown limit names the field and writes nothing."""
    config_path = aldwin_home / "config.yaml"
    before = config_path.read_text(encoding="utf-8")

    with pytest.raises(ConfigError) as exc:
        update_config_limits(updates, aldwin_home)

    assert exc.value.field == field
    assert config_path.read_text(encoding="utf-8") == before


def test_update_config_limits_preserves_surrounding_comments(aldwin_home: Path) -> None:
    """Comments around and inside the limits block survive."""
    config_path = aldwin_home / "config.yaml"
    before = config_path.read_text(encoding="utf-8")
    comments = [line for line in before.splitlines() if line.lstrip().startswith("#")]

    update_config_limits({"max_steps": 40, "connector_tools": 60}, aldwin_home)

    after = config_path.read_text(encoding="utf-8")
    for comment in comments:
        assert comment in after.splitlines()
    assert "  max_steps: 40" in after.splitlines()
    assert "  connector_tools: 60" in after.splitlines()
    assert after.index("connector_tools") < after.index("# Web search backend")
    assert load_config(aldwin_home).limits.connector_tools == 60


@pytest.mark.parametrize(
    "source",
    [
        "model: gpt-4o\n",
        "model: gpt-4o\nlimits: {}\ntheme: dark\n",
        "limits:\n",
        "model: gpt-4o\nlimits:\n  web_searches: 5",
    ],
)
def test_update_config_limits_writes_a_missing_or_flow_block(
    tmp_path: Path, source: str
) -> None:
    """A file with no block, an empty flow block or a bare header still updates."""
    (tmp_path / "config.yaml").write_text(source, encoding="utf-8")

    update_config_limits({"max_steps": 33, "notes_days": 2}, tmp_path)

    cfg = load_config(tmp_path)
    assert (cfg.limits.max_steps, cfg.limits.notes_days) == (33, 2)
    assert cfg.model == "gpt-4o"


def test_config_writers_keep_file_mode_0600(aldwin_home: Path) -> None:
    """Writes under the lock still leave config.yaml at 0600."""
    config_path = aldwin_home / "config.yaml"
    update_config_limits({"max_steps": 18}, aldwin_home)
    assert (config_path.stat().st_mode & 0o777) == 0o600
    update_config_theme("light", aldwin_home)
    assert (config_path.stat().st_mode & 0o777) == 0o600


def test_sequential_config_then_secrets_writes_succeed(aldwin_home: Path) -> None:
    """A config write then a secrets write, each locked alone, both land."""
    update_config_limits({"max_steps": 19}, aldwin_home)
    save_secrets(SecretsConfig(openai_api_key=SecretStr("sk-seq-value")), aldwin_home)

    assert load_config(aldwin_home).limits.max_steps == 19
    secrets = load_secrets(aldwin_home)
    assert secrets.openai_api_key is not None
    assert secrets.openai_api_key.get_secret_value() == "sk-seq-value"


def test_privacy_md_lists_config_yaml_lock() -> None:
    """The home folder layout in the privacy doc mentions the lock files."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "privacy.md").read_text(
        encoding="utf-8"
    )
    assert "*.yaml.lock" in doc
    assert "config.yaml and secrets.yaml" in doc


def test_update_config_limits_keeps_a_column_zero_comment_inside_the_block(
    tmp_path: Path,
) -> None:
    """A comment at column zero inside limits does not split the block."""
    (tmp_path / "config.yaml").write_text(
        "limits:\n  max_steps: 15\n# note\n  web_searches: 5\n\n# Next\n"
        "run_command:\n  env_allow: []\n",
        encoding="utf-8",
    )

    update_config_limits({"web_searches": 9, "notes_days": 2}, tmp_path)

    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert text.count("web_searches") == 1
    assert "# note" in text
    assert text.index("notes_days") < text.index("# Next")
    limits = load_config(tmp_path).limits
    assert (limits.web_searches, limits.notes_days) == (9, 2)
