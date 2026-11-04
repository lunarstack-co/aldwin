"""Provider catalogue, model reference parsing, and custom server registry."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from aldwin.core.settings import ConfigError, ProviderShape

if TYPE_CHECKING:
    from aldwin.core.settings import AldwinConfig

_LOOPBACK_HOSTNAMES = frozenset(
    {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
)


@dataclass(frozen=True)
class ModelRef:
    """A model reference parsed into provider and model components."""

    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}/{self.model}"


@dataclass(frozen=True)
class ProviderEntry:
    """A registered model provider."""

    name: str
    title: str
    shape: ProviderShape
    base_url: str
    secret: str | None
    local: bool
    docs_url: str
    signup_url: str | None = None
    env_var: str | None = None
    key_hint: str | None = None
    default_port: int | None = None


def is_loopback_host(host: str) -> bool:
    """Return True if `host` is a loopback hostname or IP address."""
    lowered = host.strip().lower()
    if not lowered:
        return False
    if lowered in _LOOPBACK_HOSTNAMES:
        return True
    if lowered.startswith("[") and lowered.endswith("]"):
        lowered = lowered[1:-1]
    try:
        ip = ip_address(lowered)
        return ip.is_loopback
    except ValueError:
        return False


def parse_model_ref(ref: str) -> ModelRef:
    """Parse `ref` into provider and model, defaulting bare IDs to `openai`.

    Splits on the first `/`. Bare IDs without a slash default to provider `openai`.
    Raises `ValueError` if `ref` is empty, only whitespace, or has an empty part.
    """
    cleaned = ref.strip()
    if not cleaned:
        raise ValueError("Model reference must not be empty.")

    if "/" in cleaned:
        provider, _, model = cleaned.partition("/")
        provider = provider.strip()
        model = model.strip()
        if not provider or not model:
            raise ValueError(f"Invalid model reference: {ref!r}")
        return ModelRef(provider=provider.lower(), model=model)

    return ModelRef(provider="openai", model=cleaned)


@lru_cache(maxsize=1)
def load_builtin_providers() -> tuple[ProviderEntry, ...]:
    """Load the built-in providers shipped with Aldwin in display order."""
    raw = (
        files("aldwin.core.assets")
        .joinpath("providers.yaml")
        .read_text(encoding="utf-8")
    )
    parsed = yaml.safe_load(raw)
    raw_providers = parsed.get("providers", []) if isinstance(parsed, dict) else []
    entries = tuple(
        ProviderEntry(
            name=str(p["name"]).strip().lower(),
            title=str(p["title"]).strip(),
            shape=p["shape"],
            base_url=str(p["base_url"]).rstrip("/"),
            secret=p.get("secret"),
            local=bool(p.get("local", False)),
            docs_url=str(p.get("docs_url", "")),
            signup_url=p.get("signup_url"),
            env_var=p.get("env_var"),
            key_hint=p.get("key_hint"),
            default_port=p.get("default_port"),
        )
        for p in raw_providers
    )
    names = [e.name for e in entries]
    if len(set(names)) != len(names):
        raise ValueError("providers.yaml has duplicate provider names")
    return entries


def list_providers(config: AldwinConfig | None = None) -> list[ProviderEntry]:
    """List all available providers (built-ins merged with custom `config.providers`).

    Returns built-ins in display order first, followed by custom providers sorted
    alphabetically by name.
    """
    builtins = load_builtin_providers()
    if config is None or not getattr(config, "providers", None):
        return list(builtins)

    merged: list[ProviderEntry] = []
    custom_map = {k.strip().lower(): v for k, v in config.providers.items()}

    for b in builtins:
        if b.name in custom_map:
            override = custom_map[b.name]
            base_url = (
                override.base_url.rstrip("/") if override.base_url else b.base_url
            )
            merged.append(
                ProviderEntry(
                    name=b.name,
                    title=override.title if override.title is not None else b.title,
                    shape=override.shape if override.shape is not None else b.shape,
                    base_url=base_url,
                    secret=override.secret if override.secret is not None else b.secret,
                    local=override.local if override.local is not None else b.local,
                    docs_url=b.docs_url,
                    signup_url=b.signup_url,
                    env_var=b.env_var,
                    key_hint=b.key_hint,
                    default_port=b.default_port,
                )
            )
        else:
            merged.append(b)

    builtin_names = {b.name for b in builtins}
    custom_names = sorted(
        name for name in custom_map.keys() if name not in builtin_names
    )
    for name in custom_names:
        c = custom_map[name]
        if not c.shape:
            raise ConfigError(
                file=Path("config.yaml"),
                field=f"providers.{name}.shape",
                expected="'openai_chat', 'anthropic', or 'ollama'",
            )
        if not c.base_url:
            raise ConfigError(
                file=Path("config.yaml"),
                field=f"providers.{name}.base_url",
                expected=(
                    "an HTTP or HTTPS URL (non-loopback http:// requires local: true)"
                ),
            )
        merged.append(
            ProviderEntry(
                name=name,
                title=c.title if c.title is not None else name,
                shape=c.shape,
                base_url=c.base_url.rstrip("/"),
                secret=c.secret,
                local=bool(c.local),
                docs_url="",
                signup_url=None,
                env_var=None,
                key_hint=None,
                default_port=None,
            )
        )

    return merged


def get_provider(name: str, config: AldwinConfig | None = None) -> ProviderEntry | None:
    """Retrieve a merged provider by name, or None if unknown."""
    lowered = name.strip().lower()
    return next((p for p in list_providers(config) if p.name == lowered), None)


def validate_model_ref(
    ref: str,
    config: AldwinConfig | None = None,
    file: Path | None = None,
) -> ModelRef:
    """Parse `ref` and verify that its provider is recognized.

    Raises `ConfigError` with known providers if the provider is unknown.
    """
    try:
        model_ref = parse_model_ref(ref)
    except ValueError as exc:
        raise ConfigError(
            file or Path("config.yaml"),
            field="model",
            expected="a non-empty string such as 'gpt-4o' or 'provider/model'",
            detail=str(exc),
        ) from None

    entry = get_provider(model_ref.provider, config)
    if entry is None:
        known_names = sorted(p.name for p in list_providers(config))
        known_str = ", ".join(known_names)
        msg = (
            f"config.yaml: model: unknown provider '{model_ref.provider}' "
            f"(known: {known_str})"
        )
        raise ConfigError(
            file=file or Path("config.yaml"),
            field="model",
            message=msg,
        )

    return model_ref
