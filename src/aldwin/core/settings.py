"""Configuration and secrets models, validation, and key resolution for Aldwin."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from aldwin.core.home import (
    DIR_MODE,
    FILE_MODE,
    atomic_write,
    ensure_permissions,
    home_dir,
)

logger = logging.getLogger("aldwin.core.settings")

CONFIG_FILENAME: Final[str] = "config.yaml"
SECRETS_FILENAME: Final[str] = "secrets.yaml"

KeySource = Literal["env", "file", "missing"]

_EXPECTED_FIELD_TYPES: Final[dict[str, str]] = {
    "model": "a non-empty string",
    "theme": "'auto', 'dark', 'light', 'colour_blind', or 'plain'",
    "last_seen_version": "a version string such as '0.0.2'",
    "check_for_updates": "true or false",
    "limits": "a mapping of limit settings",
    "limits.context_exchanges": "an integer >= 1",
    "limits.profile_chars": "an integer >= 100",
    "limits.notes_chars": "an integer >= 100",
    "limits.notes_days": "an integer >= 1",
    "limits.persona_chars": "an integer >= 100",
    "limits.max_steps": "an integer >= 1",
    "limits.web_searches": "an integer >= 1",
    "limits.connector_tools": "an integer >= 5",
    "run_command": "a mapping of run_command settings",
    "run_command.env_allow": "a list of environment variable names",
    "run_command.env_allow.*": "an environment variable name (a string)",
    "connectors": "a mapping of connector settings",
    "connectors.import_paths": "a list of paths",
    "connectors.import_paths.*": "a path (a string)",
    "search": "a mapping of web search settings",
    "search.provider": (
        "'auto', 'searxng', 'tavily', 'brave', 'exa', "
        "'perplexity', 'parallel', or 'firecrawl'"
    ),
    "search.searxng_url": "a SearXNG base URL string, or null",
    "providers": "a mapping of custom provider settings",
    "providers.*": "a mapping of provider settings",
    "providers.*.shape": "'openai_chat', 'anthropic', or 'ollama'",
    "providers.*.base_url": (
        "an HTTP or HTTPS URL (non-loopback http:// requires local: true)"
    ),
    "providers.*.secret": (
        "an environment variable name or secret key name (a string)"
    ),
    "providers.*.title": "a string display title",
    "providers.*.local": "true or false",
    "openai_api_key": "a string API key",
    "tavily_api_key": "a string API key",
}

_SECRET_ENV_ALLOW_RE: Final[re.Pattern[str]] = re.compile(
    r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL",
    re.IGNORECASE,
)


def _describe_error_location(
    loc: tuple[int | str, ...],
) -> tuple[str, str | None]:
    """Return a display name and `_EXPECTED_FIELD_TYPES` entry for `loc`.

    List indices become ``[n]`` in the name and ``*`` in the lookup key, so
    ``("run_command", "env_allow", 0)`` reads as ``run_command.env_allow[0]``.
    Dict keys under ``providers.<name>.<field>`` match ``providers.*.<field>``.
    """
    name = ""
    key_parts: list[str] = []
    for part in loc:
        if isinstance(part, int):
            name += f"[{part}]"
            key_parts.append("*")
        else:
            name = f"{name}.{part}" if name else str(part)
            key_parts.append(str(part))
    full_key = ".".join(key_parts)
    expected = _EXPECTED_FIELD_TYPES.get(full_key)
    if expected is None and len(key_parts) >= 2 and key_parts[0] == "providers":
        if len(key_parts) == 3:
            expected = _EXPECTED_FIELD_TYPES.get(f"providers.*.{key_parts[2]}")
        elif len(key_parts) == 2:
            expected = _EXPECTED_FIELD_TYPES.get("providers.*")
    return name or "<root>", expected


class ConfigError(Exception):
    """Raised when ``config.yaml`` or ``secrets.yaml`` contains invalid values."""

    def __init__(
        self,
        file: Path | str | None = None,
        field: str | None = None,
        expected: str | None = None,
        detail: str = "",
        message: str | None = None,
    ) -> None:
        if message is not None or (field is None and expected is None):
            # Single-string message form (e.g. unknown provider in model reference)
            msg = message if message is not None else str(file)
            self.file = file if isinstance(file, Path) else Path("config.yaml")
            self.field = field or "model"
            self.expected = expected or ""
            self.detail = detail
            super().__init__(msg)
        else:
            path = file if isinstance(file, Path) else Path(str(file or "config.yaml"))
            self.file = path
            self.field = field or ""
            self.expected = expected or ""
            self.detail = detail
            suffix = f" ({detail})" if detail else ""
            msg = (
                f"Invalid configuration in {path}: '{self.field}' must be "
                f"{self.expected}{suffix}. Fix the value in {path.name}, or "
                "delete the file to restore defaults."
            )
            super().__init__(msg)


class LimitsConfig(BaseModel):
    """Context and turn limits from ``config.yaml``."""

    model_config = ConfigDict(extra="ignore")

    context_exchanges: StrictInt = Field(default=20, ge=1)
    profile_chars: StrictInt = Field(default=4000, ge=100)
    notes_chars: StrictInt = Field(default=4000, ge=100)
    notes_days: StrictInt = Field(default=7, ge=1)
    persona_chars: StrictInt = Field(default=2000, ge=100)
    max_steps: StrictInt = Field(default=15, ge=1)
    # Stops one turn from spending many searches and tripping upstream rate limits.
    web_searches: StrictInt = Field(default=5, ge=1)
    connector_tools: StrictInt = Field(default=40, ge=5)


class RunCommandConfig(BaseModel):
    """Shell command environment configuration from ``config.yaml``."""

    model_config = ConfigDict(extra="ignore")

    env_allow: list[str] = Field(default_factory=list)


class ConnectorsConfig(BaseModel):
    """Connector settings from ``config.yaml``."""

    model_config = ConfigDict(extra="ignore")

    # Extra MCP config files `aldwin connectors import` looks at without a path.
    import_paths: list[str] = Field(default_factory=list)


SearchProviderName = Literal[
    "auto",
    "searxng",
    "tavily",
    "brave",
    "exa",
    "perplexity",
    "parallel",
    "firecrawl",
]
_VALID_SEARCH_PROVIDERS: Final[frozenset[str]] = frozenset(
    {
        "auto",
        "searxng",
        "tavily",
        "brave",
        "exa",
        "perplexity",
        "parallel",
        "firecrawl",
    }
)


class SearchConfig(BaseModel):
    """Web search backend settings from ``config.yaml``."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    provider: SearchProviderName = "auto"
    searxng_url: str | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def _validate_provider(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("provider must be a string")
        cleaned = value.strip().lower()
        if cleaned not in _VALID_SEARCH_PROVIDERS:
            raise ValueError(f"invalid search provider '{value}'")
        return cleaned

    @field_validator("searxng_url")
    @classmethod
    def _validate_searxng_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


ProviderShape = Literal["openai_chat", "anthropic", "ollama"]
_VALID_SHAPES: Final[frozenset[str]] = frozenset({"openai_chat", "anthropic", "ollama"})


class ProviderConfig(BaseModel):
    """Configuration for a custom or overridden provider in ``config.yaml``."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    shape: ProviderShape | None = None
    local: bool | None = None
    base_url: str | None = None
    secret: str | None = None
    title: str | None = None

    @field_validator("shape", mode="before")
    @classmethod
    def _validate_shape(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("shape must be a string")
        cleaned = value.strip().lower()
        if cleaned not in _VALID_SHAPES:
            raise ValueError(f"invalid shape '{value}'")
        return cleaned

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        from aldwin.core.providers.registry import is_loopback_host

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("base_url must be a non-empty string")
        parts = urlsplit(cleaned)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("base_url must be an HTTP or HTTPS URL")
        if parts.scheme == "http":
            host = parts.hostname or ""
            is_local = bool(info.data.get("local", False))
            if not is_loopback_host(host) and not is_local:
                raise ValueError(
                    "non-loopback http:// requires local: true (or use https://)"
                )
        return cleaned


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
    connectors: ConnectorsConfig = Field(default_factory=ConnectorsConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)

    @field_validator("providers", mode="before")
    @classmethod
    def _normalize_providers(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized: dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("provider name must be a string")
            key = raw_key.strip().lower()
            if not key:
                raise ValueError("provider name must not be empty")
            if key in normalized:
                raise ValueError(f"duplicate provider key '{raw_key}'")
            normalized[key] = raw_val
        return normalized

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


def normalize_key_field(provider_or_field: str) -> str:
    """Convert a provider name or field name into ``'<name>_api_key'``."""
    lowered = provider_or_field.strip().lower().replace("-", "_")
    if lowered.endswith(("_api_key", "_token", "_secret", "_key")):
        return lowered
    return f"{lowered}_api_key"


_normalize_key_field = normalize_key_field


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
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None

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
        for field in (
            "openai_api_key",
            "tavily_api_key",
            "anthropic_api_key",
            "gemini_api_key",
            "openrouter_api_key",
            "deepseek_api_key",
        ):
            val: SecretStr | None = getattr(self, field, None)
            if val is not None:
                v = val.get_secret_value().strip()
                if v:
                    result[field] = v
        if self.__pydantic_extra__:
            for key, extra_val in self.__pydantic_extra__.items():
                if isinstance(extra_val, SecretStr):
                    v = extra_val.get_secret_value().strip()
                    if v:
                        result[key] = v
        return result

    def __str__(self) -> str:
        parts: list[str] = [
            f"openai_api_key={(mask_secret(self.openai_api_key) or 'None')!r}",
            f"tavily_api_key={(mask_secret(self.tavily_api_key) or 'None')!r}",
            f"anthropic_api_key={(mask_secret(self.anthropic_api_key) or 'None')!r}",
            f"gemini_api_key={(mask_secret(self.gemini_api_key) or 'None')!r}",
            f"openrouter_api_key={(mask_secret(self.openrouter_api_key) or 'None')!r}",
            f"deepseek_api_key={(mask_secret(self.deepseek_api_key) or 'None')!r}",
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

    raw_search = raw.get("search")
    if isinstance(raw_search, dict):
        known_search = set(SearchConfig.model_fields.keys())
        for key in sorted(set(raw_search.keys()) - known_search):
            logger.warning("Ignoring unknown key 'search.%s' in %s", key, config_path)

    raw_providers = raw.get("providers")
    if isinstance(raw_providers, dict):
        known_provider_fields = set(ProviderConfig.model_fields.keys())
        for prov_name, prov_val in raw_providers.items():
            if isinstance(prov_val, dict):
                for key in sorted(set(prov_val.keys()) - known_provider_fields):
                    logger.warning(
                        "Ignoring unknown key 'providers.%s.%s' in %s",
                        prov_name,
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
    if "search" in parsed and parsed["search"] is None:
        parsed = {**parsed, "search": {}}
    if "providers" in parsed and parsed["providers"] is None:
        parsed = {**parsed, "providers": {}}

    try:
        cfg = AldwinConfig.model_validate(parsed)
    except ValidationError as exc:
        first = exc.errors()[0]
        field_name, expected = _describe_error_location(tuple(first.get("loc", ())))
        # Unmapped fields keep pydantic's text as detail so the sentence still reads.
        raise ConfigError(
            file=config_path,
            field=field_name,
            expected=expected or "a valid value",
            detail="" if expected else str(first.get("msg", "")),
        ) from None

    from aldwin.core.providers.registry import (
        load_builtin_providers,
        validate_model_ref,
    )

    builtin_names = {b.name for b in load_builtin_providers()}
    for prov_name, prov_cfg in cfg.providers.items():
        if prov_name.strip().lower() not in builtin_names:
            if prov_cfg.shape is None:
                raise ConfigError(
                    file=config_path,
                    field=f"providers.{prov_name}.shape",
                    expected=_EXPECTED_FIELD_TYPES["providers.*.shape"],
                )
            if prov_cfg.base_url is None:
                raise ConfigError(
                    file=config_path,
                    field=f"providers.{prov_name}.base_url",
                    expected=_EXPECTED_FIELD_TYPES["providers.*.base_url"],
                )

    validate_model_ref(cfg.model, cfg, file=config_path)

    if cfg.run_command.env_allow:
        filtered = _filter_secret_env_allow(cfg.run_command.env_allow)
        cfg = cfg.model_copy(
            update={"run_command": RunCommandConfig(env_allow=filtered)}
        )
    return cfg


@contextmanager
def _file_locked(home: Path, filename: str) -> Iterator[None]:
    """Hold an exclusive ``flock`` on ``<home>/<filename>.lock`` (mode ``0600``)."""
    import fcntl

    home.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    fd = os.open(home / f"{filename}.lock", os.O_RDWR | os.O_CREAT, FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def config_locked(home: Path) -> Iterator[None]:
    """Hold an exclusive lock for a read-modify-write of ``config.yaml``.

    Every process writing ``config.yaml`` takes this lock, so two doors never
    interleave a read and a write. To hold both locks at once, take this one
    first and ``secrets_locked`` inside it, never the reverse, or two writers
    can deadlock.
    """
    with _file_locked(home, CONFIG_FILENAME):
        yield


def config_secrets_fingerprint(home: Path) -> tuple[int, int, int, int]:
    """Return ``mtime_ns`` and size of ``config.yaml`` then ``secrets.yaml``.

    A cheap check for "another process may have changed settings". A missing
    file counts as ``(0, 0)``; lock files are ignored.
    """
    out: list[int] = []
    for name in (CONFIG_FILENAME, SECRETS_FILENAME):
        try:
            stat = (home / name).stat()
        except FileNotFoundError:
            out.extend((0, 0))
            continue
        out.extend((stat.st_mtime_ns, stat.st_size))
    return (out[0], out[1], out[2], out[3])


def _read_config_source(config_path: Path) -> str:
    """Return existing ``config.yaml`` text or the packaged default."""
    if config_path.exists():
        return config_path.read_text(encoding="utf-8")
    return (
        files("aldwin.core.assets")
        .joinpath(CONFIG_FILENAME)
        .read_text(encoding="utf-8")
    )


def _set_yaml_scalar_key(source: str, key: str, value: str | bool) -> str:
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

    from aldwin.core.providers.registry import validate_model_ref

    with config_locked(root):
        existing_cfg = load_config(root) if config_path.exists() else AldwinConfig()
        validate_model_ref(cleaned, existing_cfg, file=config_path)
        source = _read_config_source(config_path)
        updated = _set_yaml_scalar_key(source, "model", cleaned)
        atomic_write(config_path, updated, mode=FILE_MODE)


def _set_search_key_in_source(source: str, key: str, value: str | None) -> str:
    """Set ``search.<key>`` in YAML text while preserving surrounding comments."""
    serialized = yaml.safe_dump(value, default_style=None).strip()
    if serialized.endswith("\n..."):
        serialized = serialized[:-4].strip()

    lines = source.splitlines(keepends=True)
    out: list[str] = []
    in_search = False
    provider_set = False
    search_seen = False
    nested_key = re.compile(rf"^([ \t]+){re.escape(key)}\s*:")

    for line in lines:
        if re.match(r"^search\s*:", line):
            in_search = True
            search_seen = True
            out.append(line)
            continue
        if in_search:
            if (
                line.strip()
                and not line[0].isspace()
                and not line.lstrip().startswith("#")
            ):
                if not provider_set:
                    out.append(f"  {key}: {serialized}\n")
                    provider_set = True
                in_search = False
                out.append(line)
                continue
            nested = nested_key.match(line)
            if nested:
                out.append(f"{nested.group(1)}{key}: {serialized}\n")
                provider_set = True
                continue
        out.append(line)

    if search_seen and in_search and not provider_set:
        out.append(f"  {key}: {serialized}\n")
        provider_set = True

    if provider_set:
        return "".join(out)

    sep = "" if source.endswith("\n") or not source else "\n"
    return f"{source}{sep}search:\n  {key}: {serialized}\n"


def update_config_search_provider(provider: str, home: Path | None = None) -> None:
    """Persist ``search.provider`` in ``config.yaml`` while preserving comments."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME
    try:
        validated = SearchConfig.model_validate({"provider": provider}).provider
    except ValidationError as exc:
        raise ConfigError(
            file=config_path,
            field="search.provider",
            expected=_EXPECTED_FIELD_TYPES["search.provider"],
            detail=str(exc.errors()[0].get("msg", "")),
        ) from None
    with config_locked(root):
        updated = _set_search_key_in_source(
            _read_config_source(config_path), "provider", validated
        )
        atomic_write(config_path, updated, mode=FILE_MODE)


def update_config_searxng_url(url: str | None, home: Path | None = None) -> None:
    """Persist ``search.searxng_url`` (``None`` clears it), preserving comments."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME
    cleaned = url.strip() if url is not None else None
    with config_locked(root):
        updated = _set_search_key_in_source(
            _read_config_source(config_path), "searxng_url", cleaned or None
        )
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
    with config_locked(root):
        source = _read_config_source(config_path)
        updated = _set_yaml_scalar_key(source, "theme", validated)
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
    with config_locked(root):
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
    with config_locked(root):
        updated = _set_yaml_scalar_key(
            _read_config_source(config_path), "last_seen_version", cleaned
        )
        atomic_write(config_path, updated, mode=FILE_MODE)


def update_config_check_for_updates(enabled: bool, home: Path | None = None) -> None:
    """Persist ``check_for_updates`` in ``config.yaml`` while preserving comments."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME
    if not isinstance(enabled, bool):
        raise ConfigError(
            file=config_path,
            field="check_for_updates",
            expected=_EXPECTED_FIELD_TYPES["check_for_updates"],
        )
    with config_locked(root):
        updated = _set_yaml_scalar_key(
            _read_config_source(config_path), "check_for_updates", enabled
        )
        atomic_write(config_path, updated, mode=FILE_MODE)


def update_config_provider(
    name: str,
    provider: ProviderConfig,
    home: Path | None = None,
) -> None:
    """Add or update a provider under ``providers:`` in ``config.yaml``."""
    cleaned_name = name.strip().lower()
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME
    if not cleaned_name:
        raise ConfigError(
            file=config_path,
            field="providers",
            expected="a non-empty provider name",
        )
    prov_dict: dict[str, Any] = {}
    if provider.shape is not None:
        prov_dict["shape"] = provider.shape
    if provider.base_url is not None:
        prov_dict["base_url"] = provider.base_url
    if provider.title is not None:
        prov_dict["title"] = provider.title
    if provider.local is not None:
        prov_dict["local"] = provider.local
    if provider.secret is not None:
        prov_dict["secret"] = provider.secret
    with config_locked(root):
        parsed = yaml.safe_load(_read_config_source(config_path))
        if not isinstance(parsed, dict):
            parsed = {}
        providers = parsed.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            parsed["providers"] = providers
        providers[cleaned_name] = prov_dict
        serialized = yaml.safe_dump(parsed, sort_keys=False)
        atomic_write(config_path, serialized, mode=FILE_MODE)


def _set_limits_in_source(source: str, values: Mapping[str, int]) -> str:
    """Set ``limits.<key>`` values in YAML text, keeping every comment.

    Keys already in the block are replaced in place; missing keys are added at
    the end of the block, and a missing block is appended to the file.
    """
    if not values:
        return source
    lines = source.splitlines(keepends=True)
    header = next(
        (i for i, line in enumerate(lines) if re.match(r"^limits\s*:", line)), None
    )
    if header is None:
        sep = "" if source.endswith("\n") or not source else "\n"
        body = "".join(f"  {k}: {v}\n" for k, v in values.items())
        return f"{source}{sep}limits:\n{body}"

    # A flow value such as `limits: {}` becomes a block of its own.
    lines[header] = "limits:\n"
    # Comments do not close a YAML block, even at column zero; a key does.
    end = header + 1
    while end < len(lines) and (
        not lines[end].strip()
        or lines[end][0].isspace()
        or lines[end].lstrip().startswith("#")
    ):
        end += 1
    while end > header + 1 and (
        not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")
    ):
        end -= 1

    pending = dict(values)
    for i in range(header + 1, end):
        match = re.match(r"^([ \t]+)([A-Za-z_][A-Za-z0-9_]*)\s*:", lines[i])
        if match and match.group(2) in pending:
            indent, key = match.group(1), match.group(2)
            lines[i] = f"{indent}{key}: {pending.pop(key)}\n"
    added = [f"  {k}: {v}\n" for k, v in pending.items()]
    if end > 0 and not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    return "".join(lines[:end] + added + lines[end:])


def update_config_limits(
    updates: Mapping[str, Any], home: Path | None = None
) -> LimitsConfig:
    """Merge ``updates`` into ``limits`` in ``config.yaml`` as one locked rewrite.

    Every value is validated before anything is written; an unknown key or a
    bad value raises ``ConfigError`` naming ``limits.<key>`` and leaves the
    file untouched. Comments are kept. Returns the resulting limits.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config_path = root / CONFIG_FILENAME
    known = set(LimitsConfig.model_fields)
    for key in updates:
        if key not in known:
            raise ConfigError(
                file=config_path,
                field=f"limits.{key}",
                expected="one of " + ", ".join(sorted(known)),
            )

    with config_locked(root):
        current = load_config(root).limits if config_path.exists() else LimitsConfig()
        try:
            merged = LimitsConfig.model_validate(
                {**current.model_dump(), **dict(updates)}
            )
        except ValidationError as exc:
            field_name, expected = _describe_error_location(
                ("limits", *exc.errors()[0].get("loc", ()))
            )
            raise ConfigError(
                file=config_path,
                field=field_name,
                expected=expected or "a valid value",
            ) from None
        values = {key: getattr(merged, key) for key in updates}
        source = _read_config_source(config_path)
        updated = _set_limits_in_source(source, values)
        # An unchanged file keeps its mtime, so other hosts don't rebuild for nothing.
        if updated != source or not config_path.exists():
            atomic_write(config_path, updated, mode=FILE_MODE)
    return merged


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


@contextmanager
def secrets_locked(home: Path) -> Iterator[None]:
    """Hold an exclusive lock for a read-modify-write of ``secrets.yaml``.

    When both are needed, take ``config_locked`` first and this one inside it.
    """
    with _file_locked(home, SECRETS_FILENAME):
        yield


def save_secrets(secrets: SecretsConfig, home: Path | None = None) -> Path:
    """Atomically write ``secrets`` to ``secrets.yaml`` with mode ``0600``.

    Preserves any other provider keys already present in ``secrets.yaml``.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    secrets_path = root / SECRETS_FILENAME

    with secrets_locked(root):
        payload: dict[str, str] = {}
        if secrets_path.exists():
            existing = load_secrets(root)
            payload.update(existing.to_secret_dict())

        payload.update(secrets.to_secret_dict())

        serialized = yaml.safe_dump(payload, sort_keys=True) if payload else "{}\n"
        atomic_write(secrets_path, serialized, mode=FILE_MODE)
    return secrets_path


def remove_secret(field: str, home: Path | None = None) -> bool:
    """Delete one key from ``secrets.yaml``; return whether it was there.

    Other keys and the file's ``0600`` mode are kept.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    secrets_path = root / SECRETS_FILENAME
    name = normalize_key_field(field)
    with secrets_locked(root):
        if not secrets_path.exists():
            return False
        payload = load_secrets(root).to_secret_dict()
        if payload.pop(name, None) is None:
            return False
        serialized = yaml.safe_dump(payload, sort_keys=True) if payload else "{}\n"
        atomic_write(secrets_path, serialized, mode=FILE_MODE)
    return True


def resolve_key(
    provider: str,
    home: Path | None = None,
    *,
    secret_field: str | None = None,
) -> ResolvedKey:
    """Resolve an API key, checking environment variables before ``secrets.yaml``.

    Accepts any provider name (e.g. ``'openai'``, ``'tavily'``, ``'anthropic'``)
    or key field name (e.g. ``'openai_api_key'``), checking ``<PROVIDER>_API_KEY``
    in ``os.environ`` before ``secrets.yaml``.
    """
    if secret_field is not None and secret_field.strip():
        field_name = secret_field.strip().lower()
        env_var = field_name.upper()
    else:
        prov_entry = None
        try:
            from aldwin.core.providers.registry import get_provider

            prov_entry = get_provider(provider)
        except Exception:
            pass

        if prov_entry is not None:
            field_name = prov_entry.secret or _normalize_key_field(provider)
            env_var = prov_entry.env_var or field_name.upper()
        else:
            field_name = _normalize_key_field(provider)
            env_var = field_name.upper()

    env_val = os.environ.get(env_var, "").strip()
    if env_val:
        return ResolvedKey(key=env_val, source="env")

    if env_var != field_name.upper():
        fallback_env_val = os.environ.get(field_name.upper(), "").strip()
        if fallback_env_val:
            return ResolvedKey(key=fallback_env_val, source="env")

    secrets = load_secrets(home)
    file_val = secrets.get_secret(field_name)
    if file_val:
        return ResolvedKey(key=file_val, source="file")

    return ResolvedKey(key=None, source="missing")
