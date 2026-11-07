"""List, connect, switch and disconnect search providers for ``/search``."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

import httpx
from pydantic import SecretStr

from aldwin.core.settings import (
    SecretsConfig,
    load_config,
    load_secrets,
    mask_secret,
    normalize_key_field,
    remove_secret,
    save_secrets,
    update_config_search_provider,
    update_config_searxng_url,
)
from aldwin.core.tools.web_search import searxng as searxng_mod
from aldwin.core.tools.web_search.hosted import (
    HostedSearchProvider,
    KeyCheck,
    KeyCheckResult,
)
from aldwin.core.tools.web_search.searxng import (
    SearxngProvider,
    clear_searxng_detection_cache,
    detect_local_searxng,
)
from aldwin.core.tools.web_search.shared import (
    AUTO_PROVIDER_ORDER,
    build_search_provider,
    hosted_provider_classes,
)

DEFAULT_SEARXNG_URL: Final[str] = "http://127.0.0.1:8080"
SEARXNG_SIGNUP_URL: Final[str] = "https://docs.searxng.org/admin/installation.html"

KeySource = Literal["env", "file", "config", "detected"]


class DisconnectOutcome(StrEnum):
    """What disconnecting a provider did."""

    REMOVED = "removed"
    # The key is in an environment variable, which Aldwin cannot change.
    ENV_ONLY = "env_only"
    # SearXNG was found on the default port and was never saved.
    DETECTED = "detected"
    NOT_CONNECTED = "not_connected"


@dataclass(frozen=True, slots=True)
class SearchProviderInfo:
    """One row of ``/search``: who the provider is and how it is connected."""

    name: str
    title: str
    kind: Literal["local", "hosted"]
    connected: bool
    source: KeySource | None
    # Masked key (``…8f2a``) or the SearXNG address; never a full key.
    detail: str
    active: bool
    signup_url: str
    check_note: str | None
    env_var: str | None


@dataclass(frozen=True, slots=True)
class SearchOverview:
    """Everything ``/search`` shows: the saved setting, what runs, and each row."""

    setting: str
    active: str | None
    items: tuple[SearchProviderInfo, ...]


@dataclass(frozen=True, slots=True)
class ConnectResult:
    """Outcome of connecting a provider."""

    check: KeyCheckResult
    # Set when an environment variable still overrides the key just saved.
    env_override: str | None = None


@dataclass(frozen=True, slots=True)
class DisconnectResult:
    """Outcome of disconnecting a provider."""

    outcome: DisconnectOutcome
    env_var: str | None = None
    # True when a saved key was deleted even though an env var still applies.
    removed_saved_key: bool = False


def _env_key(cls: type[HostedSearchProvider]) -> tuple[str, str] | None:
    for name in (cls.env_var(), *cls.env_aliases):
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    return None


def _searxng_source(home: Path) -> tuple[KeySource | None, str]:
    configured = (load_config(home).search.searxng_url or "").strip()
    if configured:
        return "config", configured
    for name in ("SEARXNG_URL", "SEARXNG_BASE_URL"):
        value = os.environ.get(name, "").strip()
        if value:
            return "env", value
    detected = detect_local_searxng()
    if detected:
        return "detected", detected
    return None, ""


def active_search_provider_id(home: Path) -> str | None:
    """Catalogue name of the backend ``web_search`` would use now, or None."""
    provider = build_search_provider(home, detect_local=True)
    if isinstance(provider, HostedSearchProvider):
        return provider.provider_id
    if isinstance(provider, SearxngProvider):
        return "searxng"
    return None


def search_overview(home: Path) -> SearchOverview:
    """Describe every provider for ``/search``, in the automatic order."""
    secrets = load_secrets(home)
    active = active_search_provider_id(home)
    hosted = hosted_provider_classes()
    items: list[SearchProviderInfo] = []
    for name in AUTO_PROVIDER_ORDER:
        if name == "searxng":
            source, url = _searxng_source(home)
            items.append(
                SearchProviderInfo(
                    name="searxng",
                    title="SearXNG",
                    kind="local",
                    connected=source is not None,
                    source=source,
                    detail=url,
                    active=active == "searxng",
                    signup_url=SEARXNG_SIGNUP_URL,
                    check_note=None,
                    env_var="SEARXNG_URL",
                )
            )
            continue
        cls = hosted[name]
        env = _env_key(cls)
        stored = secrets.get_secret(normalize_key_field(name))
        key_source: KeySource | None = "env" if env else ("file" if stored else None)
        key = env[1] if env else stored
        items.append(
            SearchProviderInfo(
                name=name,
                title=cls.name,
                kind="hosted",
                connected=key_source is not None,
                source=key_source,
                detail=mask_secret(key) if key else "",
                active=active == name,
                signup_url=cls.signup_url,
                check_note=cls.check_note,
                env_var=env[0] if env else cls.env_var(),
            )
        )
    return SearchOverview(
        setting=load_config(home).search.provider, active=active, items=tuple(items)
    )


async def check_search_key(
    provider: str,
    key: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> KeyCheckResult:
    """Check ``key`` with ``provider`` without saving it."""
    cls = hosted_provider_classes().get(provider)
    if cls is None:
        raise ValueError(f"{provider!r} is not a hosted search provider")
    cleaned = key.strip()
    if not cleaned:
        return KeyCheckResult(KeyCheck.REJECTED, "empty key")
    if transport is None:
        return await cls(cleaned).check_key()
    async with httpx.AsyncClient(transport=transport) as client:
        return await cls(cleaned, client=client).check_key()


async def connect_search_key(
    provider: str,
    key: str,
    *,
    home: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConnectResult:
    """Check the key, and only if it works save it and use the provider."""
    check = await check_search_key(provider, key, transport=transport)
    if check.outcome is not KeyCheck.OK:
        return ConnectResult(check)
    field = normalize_key_field(provider)
    secrets = SecretsConfig.model_validate({field: SecretStr(key.strip())})
    await asyncio.to_thread(save_secrets, secrets, home)
    await asyncio.to_thread(update_config_search_provider, provider, home)
    env = _env_key(hosted_provider_classes()[provider])
    override = env[0] if env and env[1] != key.strip() else None
    return ConnectResult(check, env_override=override)


async def connect_searxng(url: str, *, home: Path) -> ConnectResult:
    """Check that ``url`` is a SearXNG instance with JSON on, then save and use it."""
    cleaned = url.strip().rstrip("/") or DEFAULT_SEARXNG_URL
    if not cleaned.startswith(("http://", "https://")):
        return ConnectResult(KeyCheckResult(KeyCheck.UNEXPECTED, "not an http(s) URL"))
    found = await asyncio.to_thread(
        searxng_mod._probe_searxng_json, cleaned, timeout=5.0
    )
    if not found:
        return ConnectResult(
            KeyCheckResult(KeyCheck.UNREACHABLE, f"no SearXNG with JSON at {cleaned}")
        )
    await asyncio.to_thread(update_config_searxng_url, cleaned, home)
    await asyncio.to_thread(update_config_search_provider, "searxng", home)
    clear_searxng_detection_cache()
    return ConnectResult(KeyCheckResult(KeyCheck.OK, cleaned))


def disconnect_search(provider: str, *, home: Path) -> DisconnectResult:
    """Forget a provider's saved key or address; fall back to automatic if in use."""
    if provider == "searxng":
        source, _ = _searxng_source(home)
        if source == "config":
            update_config_searxng_url(None, home)
            clear_searxng_detection_cache()
            outcome = DisconnectOutcome.REMOVED
        elif source == "env":
            return DisconnectResult(DisconnectOutcome.ENV_ONLY, "SEARXNG_URL")
        elif source == "detected":
            return DisconnectResult(DisconnectOutcome.DETECTED)
        else:
            return DisconnectResult(DisconnectOutcome.NOT_CONNECTED)
    else:
        cls = hosted_provider_classes().get(provider)
        if cls is None:
            raise ValueError(f"{provider!r} is not a search provider")
        removed = remove_secret(normalize_key_field(provider), home)
        env = _env_key(cls)
        if env:
            return DisconnectResult(
                DisconnectOutcome.ENV_ONLY, env[0], removed_saved_key=removed
            )
        if not removed:
            return DisconnectResult(DisconnectOutcome.NOT_CONNECTED)
        outcome = DisconnectOutcome.REMOVED
    if load_config(home).search.provider == provider:
        update_config_search_provider("auto", home)
    return DisconnectResult(outcome)
