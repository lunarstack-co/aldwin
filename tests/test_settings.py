"""Unit tests for aldwin.core.settings."""

from __future__ import annotations

import logging
import os
import re
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
    SecretsConfig,
    load_config,
    load_secrets,
    mask_secret,
    resolve_key,
    save_secrets,
    update_config_last_seen_version,
    update_config_model,
    update_config_theme,
)


def test_load_config_missing_file_returns_defaults(aldwin_home: Path) -> None:
    """TC-05: Missing config.yaml returns all default settings without error."""
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
            connector_tools=40,
        ),
    )


def test_starter_assets_are_valid(aldwin_home: Path) -> None:
    """TC-17: Starter config.yaml and packaged default skills load cleanly."""
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
    """TC-18: Empty or comment-only config.yaml returns defaults."""
    config_path = aldwin_home / "config.yaml"
    atomic_write(config_path, "# only a comment\n")

    assert load_config(aldwin_home) == AldwinConfig()


def test_load_config_invalid_value_raises_config_error(aldwin_home: Path) -> None:
    """TC-06 (unit): Invalid limits.max_steps names file, field, and expected type."""
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
    """TC-07: Unknown keys in config.yaml are ignored and logged as warnings."""
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
    """TC-19: Corrupted YAML or non-mapping root raises ConfigError."""
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
    """TC-20: Boundary validation on LimitsConfig, model, and theme."""
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
    """TC-21: update_config_model updates model: while preserving comments and 0600."""
    ensure_home(aldwin_home)
    config_path = aldwin_home / "config.yaml"

    update_config_model("gpt-4.1", aldwin_home)

    raw_text = config_path.read_text(encoding="utf-8")
    assert "# Default OpenAI chat model" in raw_text
    assert "# Context and turn limits" in raw_text
    assert (config_path.stat().st_mode & 0o777) == 0o600
    assert load_config(aldwin_home).model == "gpt-4.1"


def test_resolve_key_env_takes_priority_over_file(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-08: OPENAI_API_KEY in environment overrides secrets.yaml."""
    save_secrets(
        SecretsConfig(openai_api_key=SecretStr("sk-file-key-1111")),
        aldwin_home,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key-2222")

    resolved = resolve_key("openai", aldwin_home)
    assert resolved.key == "sk-env-key-2222"
    assert resolved.source == "env"


def test_save_and_load_secrets_permissions_and_masking(aldwin_home: Path) -> None:
    """TC-09: Secrets saved with mode 0600, valid YAML, and masked str/repr."""
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
    """TC-22: secrets.yaml found with mode 0644 is repaired to 0600 with warning."""
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
    """TC-23: Blank env var falls back to secrets.yaml; absent key returns missing."""
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
    """TC-24: mask_secret and ResolvedKey never leak short or full keys."""
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
