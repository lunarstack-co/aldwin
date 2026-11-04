"""Factory for instantiating ProviderBackend instances."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from aldwin.core.providers.base import ProviderBackend
from aldwin.core.providers.registry import ProviderEntry, get_provider
from aldwin.core.settings import ConfigError, resolve_key

if TYPE_CHECKING:
    import httpx

    from aldwin.core.settings import AldwinConfig, ProviderConfig, SecretsConfig


def resolve_provider_key(
    entry: ProviderEntry,
    secrets: SecretsConfig | None = None,
    home: Path | None = None,
) -> str | None:
    """Resolve an API key for `entry` from environment, in-memory secrets, or disk."""
    if not entry.secret:
        return None

    if entry.env_var:
        env_val = os.environ.get(entry.env_var, "").strip()
        if env_val:
            return env_val

    if secrets is not None:
        direct = secrets.get_secret(entry.secret)
        if direct:
            return direct

    return resolve_key(entry.secret, home=home).key


def get_backend(
    provider: str | ProviderEntry | ProviderConfig,
    config: AldwinConfig | None = None,
    secrets: SecretsConfig | None = None,
    *,
    home: Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ProviderBackend:
    """Instantiate a `ProviderBackend` for the given provider.

    Args:
        provider: Provider name (e.g. ``"openai"``, ``"anthropic"``),
            a ``ProviderEntry``, or a ``ProviderConfig``.
        config: Optional Aldwin configuration (holds custom providers).
        secrets: Optional in-memory secrets configuration.
        home: Optional home directory path for resolving secrets.
        transport: Optional HTTP transport (for recorded tests/fixtures).
    """
    entry: ProviderEntry
    if isinstance(provider, str):
        resolved = get_provider(provider, config)
        if resolved is None:
            raise ConfigError(
                file=Path("config.yaml"),
                field="model",
                expected=f"a known provider (got {provider!r})",
            )
        entry = resolved
    elif isinstance(provider, ProviderEntry):
        entry = provider
    else:
        # ProviderConfig
        shape = provider.shape or "openai_chat"
        base_url = (provider.base_url or "").rstrip("/")
        entry = ProviderEntry(
            name=getattr(provider, "title", "custom") or "custom",
            title=getattr(provider, "title", "Custom") or "Custom",
            shape=shape,
            base_url=base_url,
            secret=provider.secret,
            local=bool(provider.local),
            docs_url="",
        )

    api_key = resolve_provider_key(entry, secrets=secrets, home=home)

    shape_str: str = str(entry.shape).lower()
    if shape_str == "openai_chat":
        from aldwin.core.providers.openai_chat import OpenAIChatBackend

        return OpenAIChatBackend(
            entry=entry,
            api_key=api_key,
            transport=transport,
        )
    elif shape_str == "anthropic":
        from aldwin.core.providers.anthropic import AnthropicBackend

        return AnthropicBackend(
            entry=entry,
            api_key=api_key,
            transport=transport,
        )
    elif shape_str == "ollama":
        from aldwin.core.providers.ollama import OllamaBackend

        return OllamaBackend(
            entry=entry,
            api_key=api_key,
            transport=transport,
        )
    else:
        raise ConfigError(
            file=Path("config.yaml"),
            field=f"providers.{entry.name}.shape",
            expected="'openai_chat', 'anthropic', or 'ollama'",
        )
