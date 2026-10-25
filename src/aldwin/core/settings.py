"""Configuration and secrets models, validation, and key resolution for Aldwin."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from aldwin.core.home import FILE_MODE, atomic_write, ensure_permissions, home_dir

logger = logging.getLogger("aldwin.core.settings")

CONFIG_FILENAME: Final[str] = "config.yaml"
SECRETS_FILENAME: Final[str] = "secrets.yaml"

KeySource = Literal["env", "file", "missing"]

_EXPECTED_FIELD_TYPES: Final[dict[str, str]] = {
    "model": "a non-empty string",
    "theme": "'auto', 'dark', 'light', 'colour_blind', or 'plain'",
    "limits": "a mapping of limit settings",
    "limits.context_exchanges": "an integer >= 1",
    "limits.profile_chars": "an integer >= 100",
    "limits.notes_chars": "an integer >= 100",
    "limits.notes_days": "an integer >= 1",
    "limits.persona_chars": "an integer >= 100",
    "limits.max_steps": "an integer >= 1",
    "run_command": "a mapping of run_command settings",
    "run_command.env_allow": "a list of environment variable names",
    "openai_api_key": "a string API key",
    "tavily_api_key": "a string API key",
}

_SECRET_ENV_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL",
    re.IGNORECASE,
)


class ConfigError(Exception):
    """Raised when ``config.yaml`` or ``secrets.yaml`` contains invalid values."""

    def __init__(self, file: Path, field: str, expected: str, detail: str = "") -> None:
        self.file = file
        self.field = field
        self.expected = expected
        self.detail = detail
        suffix = f" ({detail})" if detail else ""
        super().__init__(
            f"Invalid configuration in {file}: '{field}' must be {expected}{suffix}. "
            f"Fix the value in {file.name}, or delete the file to restore defaults."
        )


class LimitsConfig(BaseModel):
    """Context and turn limits from ``config.yaml``."""

    model_config = ConfigDict(extra="ignore")

    context_exchanges: StrictInt = Field(default=20, ge=1)
    profile_chars: StrictInt = Field(default=4000, ge=100)
    notes_chars: StrictInt = Field(default=4000, ge=100)
    notes_days: StrictInt = Field(default=7, ge=1)
    persona_chars: StrictInt = Field(default=2000, ge=100)
    max_steps: StrictInt = Field(default=15, ge=1)


class RunCommandConfig(BaseModel):
    """Shell command environment configuration from ``config.yaml``."""

    model_config = ConfigDict(extra="ignore")

    env_allow: list[str] = Field(default_factory=list)


ThemeName = Literal["auto", "dark", "light", "colour_blind", "plain"]


class AldwinConfig(BaseModel):
    """Top-level user configuration from ``~/.aldwin/config.yaml``."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    model: str = Field(default="gpt-4o", min_length=1)
    theme: ThemeName = "dark"
    last_seen_version: str | None = None
    check_for_updates: bool = True
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    run_command: RunCommandConfig = Field(default_factory=RunCommandConfig)

    @field_validator("theme", mode="before")
    @classmethod
    def _normalize_theme(cls, value: object) -> object:
        """Accept 0.0.1 ``default`` and US spelling ``color_blind``."""
        if not isinstance(value, str):
            return value
        cleaned = value.strip().lower().replace("-", "_")
        if cleaned in {"default", ""}:
            return "dark"
        if cleaned == "color_blind":
            return "colour_blind"
        return cleaned


def mask_secret(value: str | SecretStr | None) -> str:
    """Return a masked representation of an API key (e.g. ``sk-…a1b2``).

    Never returns the middle characters of a secret. Short values (< 8 chars)
    are replaced with ``***``; empty or ``None`` values return ``""``.
    """
    if value is None:
        return ""
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    cleaned = raw.strip()
    if not cleaned:
        return ""
    if len(cleaned) < 8:
        return "***"
    if cleaned.startswith("tvly-") and len(cleaned) >= 10:
        return f"tvly-…{cleaned[-4:]}"
    if cleaned.startswith("sk-"):
        return f"sk-…{cleaned[-4:]}"
    return f"{cleaned[:3]}…{cleaned[-4:]}"


def _normalize_key_field(provider_or_field: str) -> str:
    """Convert a provider name or field name into ``'<name>_api_key'``."""
    lowered = provider_or_field.strip().lower().replace("-", "_")
    if lowered.endswith(("_api_key", "_token", "_secret", "_key")):
        return lowered
    return f"{lowered}_api_key"


class SecretsConfig(BaseModel):
    """API keys stored in ``~/.aldwin/secrets.yaml`` (mode ``0600``).

    Provides typed attributes for ``openai_api_key`` and ``tavily_api_key`` while
    allowing any additional provider keys (e.g. ``anthropic_api_key``,
    ``brave_api_key``) via ``extra="allow"``. All keys are wrapped in
    ``SecretStr`` and masked in ``str()`` / ``repr()``.
    """

    model_config = ConfigDict(extra="allow")

    openai_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _wrap_extra_secrets(self) -> SecretsConfig:
        extra = self.__pydantic_extra__
        if not extra:
            return self
        for key, val in list(extra.items()):
            if val is None:
                del extra[key]
            elif isinstance(val, SecretStr):
                cleaned = val.get_secret_value().strip()
                if cleaned:
                    extra[key] = SecretStr(cleaned)
                else:
                    del extra[key]
            elif isinstance(val, str):
                cleaned = val.strip()
                if cleaned:
                    extra[key] = SecretStr(cleaned)
                else:
                    del extra[key]
            else:
                raise ValueError(f"Secret '{key}' must be a string")
        return self

    def get_secret(self, provider_or_field: str) -> str | None:
        """Return the unmasked secret string for ``provider_or_field``, or ``None``."""
        field_name = _normalize_key_field(provider_or_field)
        val: Any = getattr(self, field_name, None)
        if val is None and self.__pydantic_extra__:
            val = self.__pydantic_extra__.get(field_name)
        if isinstance(val, SecretStr):
            cleaned = val.get_secret_value().strip()
            return cleaned or None
        if isinstance(val, str):
            cleaned = val.strip()
            return cleaned or None
        return None

    def to_secret_dict(self) -> dict[str, str]:
        """Return all non-empty secrets as a plain ``dict[str, str]`` for saving."""
        result: dict[str, str] = {}
        if self.openai_api_key is not None:
            v = self.openai_api_key.get_secret_value().strip()
            if v:
                result["openai_api_key"] = v
        if self.tavily_api_key is not None:
            v = self.tavily_api_key.get_secret_value().strip()
            if v:
                result["tavily_api_key"] = v
        if self.__pydantic_extra__:
            for key, val in self.__pydantic_extra__.items():
                if isinstance(val, SecretStr):
                    v = val.get_secret_value().strip()
                    if v:
                        result[key] = v
        return result

    def __str__(self) -> str:
        parts: list[str] = [
            f"openai_api_key={(mask_secret(self.openai_api_key) or 'None')!r}",
            f"tavily_api_key={(mask_secret(self.tavily_api_key) or 'None')!r}",
        ]
        if self.__pydantic_extra__:
            for key in sorted(self.__pydantic_extra__.keys()):
                val = self.__pydantic_extra__[key]
                masked = (
                    mask_secret(val) if isinstance(val, (str, SecretStr)) else "***"
                )
                parts.append(f"{key}={(masked or 'None')!r}")
        return f"SecretsConfig({', '.join(parts)})"

    def __repr__(self) -> str:
        return self.__str__()


@dataclass(frozen=True)
class ResolvedKey:
    """An effective API key and the source it was loaded from."""

    key: str | None
    source: KeySource

    def __str__(self) -> str:
        masked = mask_secret(self.key) or None
        return f"ResolvedKey(key={masked!r}, source={self.source!r})"

    def __repr__(self) -> str:
        return self.__str__()


def _warn_unknown_config_keys(raw: dict[str, Any], config_path: Path) -> None:
    """Log a warning for any unknown keys in ``config.yaml``."""
    known_top = set(AldwinConfig.model_fields.keys())
    for key in sorted(set(raw.keys()) - known_top):
        logger.warning("Ignoring unknown key '%s' in %s", key, config_path)

    raw_limits = raw.get("limits")
    if isinstance(raw_limits, dict):
        known_limits = set(LimitsConfig.model_fields.keys())
        for key in sorted(set(raw_limits.keys()) - known_limits):
            logger.warning("Ignoring unknown key 'limits.%s' in %s", key, config_path)

    raw_run_cmd = raw.get("run_command")
    if isinstance(raw_run_cmd, dict):
        known_run_cmd = set(RunCommandConfig.model_fields.keys())
        for key in sorted(set(raw_run_cmd.keys()) - known_run_cmd):
            logger.warning(
                "Ignoring unknown key 'run_command.%s' in %s",
                key,
                config_path,
            )


def _filter_secret_env_allow(names: list[str]) -> list[str]:
    """Filter secret-shaped variable names from `run_command.env_allow`."""
    safe: list[str] = []
    for raw_name in names:
        cleaned = raw_name.strip()
        if not cleaned:
            continue
        if _SECRET_ENV_ALLOW_RE.search(cleaned):
            logger.warning(
                "run_command.env_allow: %s ignored (looks like a secret)",
                cleaned,
            )
            continue
        safe.append(cleaned)
    return safe


def load_config(home: Path | None = None) -> AldwinConfig:
    """Load and validate ``config.yaml`` from ``home`` (or ``home_dir``).

    Returns default ``AldwinConfig`` if the file does not exist or is empty.
    Logs warnings for unknown keys and raises ``ConfigError`` on invalid YAML or
    invalid field types.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME
    if not config_path.exists():
        return AldwinConfig()

    try:
        text = config_path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ConfigError(
            file=config_path,
            field="<yaml>",
            expected="valid YAML syntax",
        ) from None

    if parsed is None:
        return AldwinConfig()

    if not isinstance(parsed, dict):
        raise ConfigError(
            file=config_path,
            field="<root>",
            expected="a YAML mapping of key/value settings",
        )

    _warn_unknown_config_keys(parsed, config_path)
    if "limits" in parsed and parsed["limits"] is None:
        parsed = {**parsed, "limits": {}}
    if "run_command" in parsed and parsed["run_command"] is None:
        parsed = {**parsed, "run_command": {}}

    try:
        cfg = AldwinConfig.model_validate(parsed)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first.get("loc", ())) or "<root>"
        expected = _EXPECTED_FIELD_TYPES.get(loc, first.get("msg", "a valid value"))
        raise ConfigError(
            file=config_path,
            field=loc,
            expected=expected,
        ) from None

    if cfg.run_command.env_allow:
        filtered = _filter_secret_env_allow(cfg.run_command.env_allow)
        cfg = cfg.model_copy(
            update={"run_command": RunCommandConfig(env_allow=filtered)}
        )
    return cfg


def _read_config_source(config_path: Path) -> str:
    """Return existing ``config.yaml`` text or the packaged default."""
    if config_path.exists():
        return config_path.read_text(encoding="utf-8")
    return (
        files("aldwin.core.assets")
        .joinpath(CONFIG_FILENAME)
        .read_text(encoding="utf-8")
    )


def _set_yaml_scalar_key(source: str, key: str, value: str) -> str:
    """Replace or append a top-level scalar ``key: value`` while keeping comments."""
    serialized = yaml.safe_dump(value, default_style=None).strip()
    if serialized.endswith("\n..."):
        serialized = serialized[:-4].strip()
    pattern = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*:\s*).*$")
    if pattern.search(source):
        return pattern.sub(lambda m: f"{m.group(1)}{serialized}", source, count=1)
    sep = "" if source.endswith("\n") or not source else "\n"
    return f"{source}{sep}{key}: {serialized}\n"


def update_config_model(model: str, home: Path | None = None) -> None:
    """Update the ``model`` setting in ``config.yaml`` while preserving comments."""
    cleaned = model.strip()
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME

    if not cleaned:
        raise ConfigError(
            file=config_path,
            field="model",
            expected="a non-empty string",
        )

    updated = _set_yaml_scalar_key(_read_config_source(config_path), "model", cleaned)
    atomic_write(config_path, updated, mode=FILE_MODE)


def update_config_theme(theme: str, home: Path | None = None) -> None:
    """Update the ``theme`` setting in ``config.yaml`` while preserving comments."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME
    try:
        validated = AldwinConfig.model_validate({"theme": theme}).theme
    except ValidationError as exc:
        raise ConfigError(
            file=config_path,
            field="theme",
            expected=_EXPECTED_FIELD_TYPES["theme"],
            detail=str(exc.errors()[0].get("msg", "")),
        ) from None
    updated = _set_yaml_scalar_key(_read_config_source(config_path), "theme", validated)
    atomic_write(config_path, updated, mode=FILE_MODE)


_RUN_COMMAND_ENV_ALLOW_BLOCK: Final[str] = (
    "\n"
    "# Extra environment variable names (beyond a minimal safe set) passed to\n"
    "# run_command; secret-looking names are always ignored\n"
    "run_command:\n"
    "  env_allow: []\n"
)


def ensure_run_command_env_allow(home: Path | None = None) -> None:
    """Add a documented ``run_command: env_allow: []`` section if one is missing.

    Preserves every existing value and comment in ``config.yaml``; a no-op when
    a top-level ``run_command`` key is already present.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME
    source = _read_config_source(config_path)
    if re.search(r"(?m)^run_command\s*:", source):
        return
    sep = "" if source.endswith("\n") or not source else "\n"
    atomic_write(
        config_path, f"{source}{sep}{_RUN_COMMAND_ENV_ALLOW_BLOCK}", mode=FILE_MODE
    )


def update_config_last_seen_version(version: str, home: Path | None = None) -> None:
    """Persist ``last_seen_version`` for upgrade-once What's new / splash gates."""
    cleaned = version.strip()
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME
    if not cleaned:
        raise ConfigError(
            file=config_path,
            field="last_seen_version",
            expected="a non-empty version string",
        )
    updated = _set_yaml_scalar_key(
        _read_config_source(config_path), "last_seen_version", cleaned
    )
    atomic_write(config_path, updated, mode=FILE_MODE)


def load_secrets(home: Path | None = None) -> SecretsConfig:
    """Load ``secrets.yaml`` from ``home``, enforcing mode ``0600``."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    secrets_path = root / SECRETS_FILENAME
    if not secrets_path.exists():
        return SecretsConfig()

    ensure_permissions(secrets_path, FILE_MODE)

    try:
        text = secrets_path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ConfigError(
            file=secrets_path,
            field="<yaml>",
            expected="valid YAML syntax",
        ) from None

    if parsed is None:
        return SecretsConfig()

    if not isinstance(parsed, dict):
        raise ConfigError(
            file=secrets_path,
            field="<root>",
            expected="a YAML mapping of secret keys",
        )

    cleaned_dict: dict[str, Any] = {}
    for key, val in parsed.items():
        if val is None:
            continue
        if not isinstance(val, str):
            raise ConfigError(
                file=secrets_path,
                field=str(key),
                expected=_EXPECTED_FIELD_TYPES.get(str(key), "a string API key"),
            )
        stripped = val.strip()
        if stripped:
            cleaned_dict[str(key)] = stripped

    try:
        return SecretsConfig.model_validate(cleaned_dict)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first.get("loc", ())) or "<root>"
        raise ConfigError(
            file=secrets_path,
            field=loc,
            expected=_EXPECTED_FIELD_TYPES.get(loc, "a string API key"),
        ) from None


def save_secrets(secrets: SecretsConfig, home: Path | None = None) -> Path:
    """Atomically write ``secrets`` to ``secrets.yaml`` with mode ``0600``.

    Preserves any other provider keys already present in ``secrets.yaml``.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    secrets_path = root / SECRETS_FILENAME

    payload: dict[str, str] = {}
    if secrets_path.exists():
        existing = load_secrets(root)
        payload.update(existing.to_secret_dict())

    payload.update(secrets.to_secret_dict())

    serialized = yaml.safe_dump(payload, sort_keys=True) if payload else "{}\n"
    atomic_write(secrets_path, serialized, mode=FILE_MODE)
    return secrets_path


def resolve_key(provider: str, home: Path | None = None) -> ResolvedKey:
    """Resolve an API key, checking environment variables before ``secrets.yaml``.

    Accepts any provider name (e.g. ``'openai'``, ``'tavily'``, ``'anthropic'``)
    or key field name (e.g. ``'openai_api_key'``), checking ``<PROVIDER>_API_KEY``
    in ``os.environ`` before ``secrets.yaml``.
    """
    field_name = _normalize_key_field(provider)
    env_var = field_name.upper()
    env_val = os.environ.get(env_var, "").strip()
    if env_val:
        return ResolvedKey(key=env_val, source="env")

    secrets = load_secrets(home)
    file_val = secrets.get_secret(field_name)
    if file_val:
        return ResolvedKey(key=file_val, source="file")

    return ResolvedKey(key=None, source="missing")
