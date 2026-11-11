"""API key validation and credential storage helpers for model providers."""

from __future__ import annotations

import logging
import os
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import SecretStr

if TYPE_CHECKING:
    import httpx

from aldwin.core.home import home_dir
from aldwin.core.providers.base import AccessStatus, ProviderBackend
from aldwin.core.providers.factory import get_backend
from aldwin.core.providers.registry import ProviderEntry, get_provider
from aldwin.core.settings import (
    AldwinConfig,
    SecretsConfig,
    load_config,
    normalize_key_field,
    remove_secret,
    save_secrets,
)

logger = logging.getLogger("aldwin.core.providers.keys")


def resolve_provider_secret_field(
    provider: str,
    config: AldwinConfig | None = None,
    home: Path | None = None,
) -> str:
    """Return the secrets.yaml field name for `provider`."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    active_config = config
    if active_config is None and (root / "config.yaml").exists():
        active_config = load_config(root)
    entry = get_provider(provider, active_config)
    if entry is not None and entry.secret:
        return entry.secret
    return normalize_key_field(provider)


async def check_provider_key(
    provider: str,
    key: str,
    *,
    home: Path | None = None,
    base_url: str | None = None,
    config: AldwinConfig | None = None,
    backend: ProviderBackend | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AccessStatus:
    """Check a candidate API key with the provider without saving it."""
    cleaned_key = key.strip()
    if not cleaned_key:
        return AccessStatus.INVALID_KEY

    root = (home if home is not None else home_dir()).expanduser().resolve()
    active_config = config
    if active_config is None and (root / "config.yaml").exists():
        active_config = load_config(root)
    entry = get_provider(provider, active_config)

    if entry is not None and (entry.local or not entry.secret):
        raise ValueError(f"Provider '{provider}' does not use an API key.")

    active_backend: ProviderBackend
    if backend is not None:
        active_backend = backend
    else:
        secret_field = resolve_provider_secret_field(provider, active_config, root)
        candidate_secrets = SecretsConfig(**{secret_field: SecretStr(cleaned_key)})
        target_provider: str | ProviderEntry = provider
        if base_url is not None and base_url.strip() and entry is not None:
            from dataclasses import replace

            target_provider = replace(entry, base_url=base_url.strip().rstrip("/"))
        active_backend = get_backend(
            target_provider,
            config=active_config,
            secrets=candidate_secrets,
            home=root,
            transport=transport,
        )

    try:
        return await active_backend.check_access()
    except Exception as exc:
        logger.warning("Error checking provider '%s' key: %s", provider, exc)
        return AccessStatus.UNREACHABLE


async def save_provider_key(
    provider: str,
    key: str,
    *,
    home: Path | None = None,
    base_url: str | None = None,
    config: AldwinConfig | None = None,
    backend: ProviderBackend | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AccessStatus:
    """Validate a candidate API key and persist to secrets.yaml only on success.

    Leaves secrets.yaml completely untouched if validation does not return OK.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    active_config = config
    if active_config is None and (root / "config.yaml").exists():
        active_config = load_config(root)
    status = await check_provider_key(
        provider,
        key,
        home=root,
        base_url=base_url,
        config=active_config,
        backend=backend,
        transport=transport,
    )
    if status != AccessStatus.OK:
        return status

    secret_field = resolve_provider_secret_field(provider, active_config, root)
    # save_secrets merges under its lock; a pre-read here would race other writers.
    save_secrets(SecretsConfig(**{secret_field: SecretStr(key.strip())}), root)
    return AccessStatus.OK


class ServerAddressError(ValueError):
    """A custom server address that Aldwin will not connect to."""


async def connect_provider_server(
    name: str,
    base_url: str,
    key: str | None = None,
    *,
    home: Path | None = None,
    title: str | None = None,
    local: bool = False,
    config: AldwinConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AccessStatus:
    """Check an OpenAI-compatible server and, if it answers, add it to config.

    The key, when given, is saved only after the server accepts it. Raises
    ``ServerAddressError`` for an address that is not http(s), or plain http
    to another computer.
    """
    import asyncio
    from urllib.parse import urlsplit

    from aldwin.core.providers.registry import is_loopback_host
    from aldwin.core.settings import ProviderConfig, update_config_provider

    root = (home if home is not None else home_dir()).expanduser().resolve()
    cleaned_name = name.strip().lower()
    cleaned_url = base_url.strip().rstrip("/")
    cleaned_key = (key or "").strip()
    parsed = urlsplit(cleaned_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ServerAddressError(
            f"Invalid server address {cleaned_url!r}. "
            "Address must begin with http:// or https://."
        )
    is_local = local or (
        parsed.scheme == "http" and is_loopback_host(parsed.hostname or "")
    )
    if parsed.scheme == "http" and not is_local:
        raise ServerAddressError(f"Non-local server {cleaned_url!r} must use https://.")

    provider_cfg = ProviderConfig(
        shape="openai_chat",
        base_url=cleaned_url,
        title=title or cleaned_name,
        local=is_local,
        secret=f"{cleaned_name}_api_key" if cleaned_key else None,
    )
    candidate = ProviderEntry(
        name=cleaned_name,
        title=provider_cfg.title or cleaned_name,
        shape="openai_chat",
        base_url=cleaned_url,
        secret=provider_cfg.secret,
        local=is_local,
        docs_url="",
    )
    candidate_secrets = (
        SecretsConfig(**{f"{cleaned_name}_api_key": SecretStr(cleaned_key)})
        if cleaned_key
        else SecretsConfig()
    )
    active_config = config
    if active_config is None and (root / "config.yaml").exists():
        active_config = load_config(root)
    backend = get_backend(
        candidate,
        config=active_config,
        secrets=candidate_secrets,
        home=root,
        transport=transport,
    )
    try:
        status = await asyncio.wait_for(backend.check_access(), timeout=5.0)
    except Exception as exc:
        logger.warning(
            "Failed to verify server %s at %s: %s", cleaned_name, cleaned_url, exc
        )
        status = AccessStatus.UNREACHABLE
    if status != AccessStatus.OK:
        return status

    update_config_provider(cleaned_name, provider_cfg, root)
    if cleaned_key:
        save_secrets(
            SecretsConfig(**{f"{cleaned_name}_api_key": SecretStr(cleaned_key)}), root
        )
    return AccessStatus.OK


class KeyRemoval(StrEnum):
    """What removing a model provider's key did."""

    REMOVED = "removed"
    # The key is in an environment variable, which Aldwin cannot change.
    ENV_ONLY = "env_only"
    # The current model runs on this provider.
    IN_USE = "in_use"
    NOT_CONNECTED = "not_connected"


def provider_of_model(model_ref: str) -> str:
    """Provider name of a model reference (``openai`` for a bare model id)."""
    cleaned = model_ref.strip()
    return cleaned.split("/", 1)[0].lower() if "/" in cleaned else "openai"


def remove_provider_key(
    provider: str,
    *,
    current_model: str,
    home: Path | None = None,
    config: AldwinConfig | None = None,
) -> tuple[KeyRemoval, str | None]:
    """Remove a cloud provider's key from secrets.yaml.

    Returns the outcome and, for ``ENV_ONLY``, the environment variable name.
    The provider of the current model is never disconnected.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    active_config = config
    if active_config is None and (root / "config.yaml").exists():
        active_config = load_config(root)
    name = provider.strip().lower()
    if provider_of_model(current_model) == name:
        return KeyRemoval.IN_USE, None
    field = resolve_provider_secret_field(name, active_config, root)
    entry = get_provider(name, active_config)
    env_var = (entry.env_var if entry is not None else None) or field.upper()
    removed = remove_secret(field, root)
    if os.environ.get(env_var, "").strip():
        return KeyRemoval.ENV_ONLY, env_var
    return (KeyRemoval.REMOVED if removed else KeyRemoval.NOT_CONNECTED), None
