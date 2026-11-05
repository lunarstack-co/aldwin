"""OpenAI model discovery, API key verification, and error classification."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Any, Final, Literal

import httpx
from agentling import (
    ModelAuthError,
    ModelBadRequestError,
    ModelConnectionError,
    ModelContextLengthError,
    ModelError,
    ModelOutputTruncatedError,
    ModelRateLimitError,
    ModelServerError,
    ModelTimeoutError,
)

from aldwin.core.logging import _redact_text
from aldwin.core.providers.openai_chat import (
    OpenAIChatModel,
    error_for_response,
    is_chat_model,
)
from aldwin.core.providers.registry import get_provider, parse_model_ref

logger = logging.getLogger("aldwin.core.models")

ModelErrorCode = Literal[
    "auth_failed",
    "rate_limited",
    "model_unavailable",
    "unknown_model",
    "request_rejected",
    "internal",
]

DEFAULT_MODEL: Final[str] = "gpt-4o"
DEFAULT_KEY_CHECK_TIMEOUT: Final[float] = 10.0
# OpenAI's 400 when a request offers more tools than it accepts.
_TOO_MANY_TOOLS_RE: Final[re.Pattern[str]] = re.compile(
    r"'tools'.*maximum length (\d+), but got an array with length (\d+)"
)

_FIRST_SHIP_TITLES: Final[dict[str, str]] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "ollama": "Ollama",
    "lmstudio": "LM Studio",
    "openrouter": "OpenRouter",
}


def parse_model_ref_parts(model: str | None) -> tuple[str, str]:
    """Return (provider_id, model_id), defaulting bare models to 'openai'."""
    raw = (model or "").strip()
    if not raw:
        return ("openai", DEFAULT_MODEL)
    try:
        ref = parse_model_ref(raw)
        return (ref.provider, ref.model)
    except ValueError:
        return ("openai", raw)


def resolve_provider_title(provider_id: str, default: str = "OpenAI") -> str:
    """Return human-friendly provider title for error messages and status lines."""
    cleaned = provider_id.strip().lower()
    if cleaned in _FIRST_SHIP_TITLES:
        return _FIRST_SHIP_TITLES[cleaned]
    entry = get_provider(cleaned)
    if entry is not None and entry.title:
        return entry.title
    return cleaned.capitalize() if cleaned else default


def is_local_model(
    model: str | None = None,
    exc: BaseException | None = None,
    *,
    local: bool = False,
) -> bool:
    """Return True if the model or exception represents a local runtime."""
    if local:
        return True
    if exc is not None and bool(getattr(exc, "local", False)):
        return True
    provider_id, _ = parse_model_ref_parts(model)
    if provider_id in ("ollama", "lmstudio"):
        return True
    entry = get_provider(provider_id)
    if entry is not None and entry.local:
        return True
    return False


RetryCallback = Callable[[int, int, BaseException, float], Awaitable[None] | None]


class KeyStatus(StrEnum):
    """Result of validating an OpenAI API key against the models endpoint."""

    OK = "ok"
    INVALID = "invalid"
    NO_ACCESS = "no_access"
    NETWORK_ERROR = "network_error"


async def list_chat_models(
    api_key: str,
    *,
    base_url: str | None = None,
    timeout: float = DEFAULT_KEY_CHECK_TIMEOUT,
    transport: httpx.AsyncBaseTransport | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Fetch OpenAI models accessible to `api_key`, filtered to chat models.

    Sorted newest first by `created` timestamp (tie-broken alphabetically by ID).
    """
    cleaned_key = api_key.strip()
    if not cleaned_key:
        raise ValueError("API key must not be empty.")

    root_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    if client is None:
        from aldwin.core.providers.openai_chat import OpenAIChatBackend
        from aldwin.core.providers.registry import ProviderEntry

        entry = ProviderEntry(
            name="openai",
            title="OpenAI",
            shape="openai_chat",
            base_url=root_url,
            secret="openai_api_key",
            local=False,
            docs_url="",
        )
        backend = OpenAIChatBackend(
            entry,
            api_key=cleaned_key,
            transport=transport,
            timeout=timeout,
        )
        infos = await backend.list_models()
        return [m.id for m in infos]

    headers = {"Authorization": f"Bearer {cleaned_key}"}
    try:
        async with asyncio.timeout(timeout):
            response = await client.get(f"{root_url}/models", headers=headers)
    except TimeoutError as exc:
        raise ModelTimeoutError(f"Request timed out: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise ModelTimeoutError(f"Request timed out: {exc}") from exc
    except httpx.TransportError as exc:
        raise ModelConnectionError(f"Could not reach {root_url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise ModelServerError(f"Bad response from {root_url}: {exc}") from exc

    if response.status_code != 200:
        raise error_for_response(response)

    try:
        payload = response.json()
    except ValueError as exc:
        raise ModelServerError(
            f"Invalid JSON from {root_url}/models: {response.text[:200]!r}"
        ) from exc

    raw_items: Sequence[Any] = (
        payload.get("data", []) if isinstance(payload, dict) else []
    )
    by_id: dict[str, int] = {}
    for item in raw_items:
        if isinstance(item, dict):
            model_id = str(item.get("id", "")).strip()
            created_raw = item.get("created", 0)
        else:
            model_id = str(getattr(item, "id", "")).strip()
            created_raw = getattr(item, "created", 0)
        if not is_chat_model(model_id):
            continue
        created = int(created_raw) if isinstance(created_raw, (int, float)) else 0
        if model_id not in by_id or created > by_id[model_id]:
            by_id[model_id] = created

    sorted_pairs = sorted(
        by_id.items(),
        key=lambda pair: (-pair[1], pair[0]),
    )
    return [model_id for model_id, _ in sorted_pairs]


async def check_key(
    api_key: str,
    *,
    base_url: str | None = None,
    timeout: float = DEFAULT_KEY_CHECK_TIMEOUT,
    transport: httpx.AsyncBaseTransport | None = None,
    client: httpx.AsyncClient | None = None,
) -> KeyStatus:
    """Validate `api_key` via the models list endpoint with a 10 s timeout."""
    cleaned_key = api_key.strip()
    if not cleaned_key:
        return KeyStatus.INVALID

    if client is None:
        from aldwin.core.providers.base import AccessStatus
        from aldwin.core.providers.openai_chat import OpenAIChatBackend
        from aldwin.core.providers.registry import ProviderEntry

        root_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        entry = ProviderEntry(
            name="openai",
            title="OpenAI",
            shape="openai_chat",
            base_url=root_url,
            secret="openai_api_key",
            local=False,
            docs_url="",
        )
        backend = OpenAIChatBackend(
            entry,
            api_key=cleaned_key,
            transport=transport,
            timeout=timeout,
        )
        access = await backend.check_access()
        if access == AccessStatus.OK:
            return KeyStatus.OK
        if access == AccessStatus.NO_ACCESS:
            return KeyStatus.NO_ACCESS
        if access == AccessStatus.INVALID_KEY:
            return KeyStatus.INVALID
        return KeyStatus.NETWORK_ERROR

    try:
        models = await list_chat_models(
            cleaned_key,
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            client=client,
        )
    except ModelAuthError as exc:
        if exc.status == 403 or "403" in str(exc):
            return KeyStatus.NO_ACCESS
        return KeyStatus.INVALID
    except ModelBadRequestError as exc:
        if exc.status == 403 or "403" in str(exc):
            return KeyStatus.NO_ACCESS
        return KeyStatus.NETWORK_ERROR
    except (
        ModelConnectionError,
        ModelTimeoutError,
        ModelRateLimitError,
        ModelServerError,
        ModelError,
    ):
        return KeyStatus.NETWORK_ERROR

    if not models:
        return KeyStatus.NO_ACCESS
    return KeyStatus.OK


def classify_model_error(
    exc: BaseException,
    *,
    model: str | None = None,
    provider_title: str | None = None,
    local: bool = False,
) -> tuple[ModelErrorCode, str]:
    """Map an Agentling ModelError to `(ModelErrorCode, message)`.

    Protocol codes stay stable (`unknown_model`, `model_unavailable`, …); the
    CLI maps them to house copy. Optional `model` names the configured model on
    not-found errors.
    """
    provider_id, model_id = parse_model_ref_parts(model)
    has_explicit_model = bool(model and model.strip())
    title = (
        provider_title.strip()
        if provider_title and provider_title.strip()
        else resolve_provider_title(provider_id)
    )
    is_local = is_local_model(model, exc=exc, local=local)

    if isinstance(exc, ModelAuthError):
        return (
            "auth_failed",
            f"Your {title} key was rejected. Run `aldwin setup` to replace it.",
        )
    if isinstance(exc, ModelRateLimitError):
        return (
            "rate_limited",
            f"{title} rate limit reached. Wait a moment and try again.",
        )
    if isinstance(exc, ModelServerError):
        return (
            "rate_limited",
            f"{title} returned a server error. Wait a moment and try again.",
        )
    if isinstance(exc, (ModelConnectionError, ModelTimeoutError)):
        if is_local:
            return (
                "model_unavailable",
                f"{title} isn't running. Start it and try again.",
            )
        return (
            "model_unavailable",
            f"Can't reach {title}. Check your connection, "
            "or switch to a local model with `/model`.",
        )
    if isinstance(exc, ModelBadRequestError):
        if (
            exc.status == 404
            or "404" in str(exc).lower()
            or "not found" in str(exc).lower()
        ):
            if is_local:
                installed_part = f"model {model_id}" if has_explicit_model else "model"
                return (
                    "unknown_model",
                    f"The {installed_part} isn't installed in {title}. "
                    "Pick another with `/model`.",
                )
            prefix = f"Model {model_id}" if has_explicit_model else "That model"
            return (
                "unknown_model",
                f"{prefix} isn't available to your key. Pick another with `/model`.",
            )
        too_many = _TOO_MANY_TOOLS_RE.search(str(exc))
        if too_many is not None:
            limit, count = too_many.groups()
            return (
                "request_rejected",
                f"OpenAI allows at most {limit} tools and {count} are switched on. "
                "Turn some off in /connectors.",
            )
        status_label = f"HTTP {exc.status}" if exc.status else "error"
        return (
            "request_rejected",
            f"{title} refused the request ({status_label}). "
            "Details are in ~/.aldwin/logs/aldwin.log.",
        )
    if isinstance(exc, ModelContextLengthError):
        target = model_id if has_explicit_model else "this model"
        return (
            "request_rejected",
            f"This conversation is too long for {target}. Start a fresh one with /new.",
        )
    if isinstance(exc, ModelOutputTruncatedError):
        return (
            "request_rejected",
            "The answer was cut off because it reached the model's length limit.",
        )
    if isinstance(exc, ModelError):
        text = _redact_text(str(exc)).lower()
        if "401" in text or "authentication" in text or "api key" in text:
            return (
                "auth_failed",
                f"Your {title} key was rejected. Run `aldwin setup` to replace it.",
            )
        if "429" in text or "rate limit" in text:
            return (
                "rate_limited",
                f"{title} rate limit reached. Wait a moment and try again.",
            )
        if (
            "500" in text
            or "502" in text
            or "503" in text
            or "504" in text
            or "server" in text
        ):
            return (
                "rate_limited",
                f"{title} returned a server error. Wait a moment and try again.",
            )
        if "404" in text or "not found" in text:
            if is_local:
                installed_part = f"model {model_id}" if has_explicit_model else "model"
                return (
                    "unknown_model",
                    f"The {installed_part} isn't installed in {title}. "
                    "Pick another with `/model`.",
                )
            prefix = f"Model {model_id}" if has_explicit_model else "That model"
            return (
                "unknown_model",
                f"{prefix} isn't available to your key. Pick another with `/model`.",
            )
        if is_local:
            return (
                "model_unavailable",
                f"{title} isn't running. Start it and try again.",
            )
        return (
            "model_unavailable",
            f"Can't reach {title}. Check your connection, "
            "or switch to a local model with `/model`.",
        )
    return (
        "internal",
        "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log.",
    )


def retry_status_message(
    exc: BaseException,
    *,
    attempt: int,
    max_retries: int,
    delay_s: float,
    provider_title: str | None = None,
    model: str | None = None,
) -> str:
    """User-facing retry line for a transient model error (host → StatusEvent)."""
    delay_label = f"{int(delay_s)}s" if delay_s >= 1 else f"{delay_s:.1f}s"
    progress = f"({attempt}/{max_retries})"
    title = (
        provider_title.strip()
        if provider_title and provider_title.strip()
        else (
            resolve_provider_title(parse_model_ref_parts(model)[0])
            if model
            else "OpenAI"
        )
    )
    if isinstance(exc, (ModelConnectionError, ModelTimeoutError)):
        return f"Can't reach {title}. Retrying in {delay_label} {progress}…"
    if (
        isinstance(exc, ModelServerError)
        or "server" in str(exc).lower()
        or bool(re.search(r"\b50[0-4]\b", str(exc)))
    ):
        return f"{title} returned a server error, retrying in {delay_label} {progress}…"
    return f"{title} is busy, retrying in {delay_label} {progress}…"


def build_openai_model(
    model_name: str,
    api_key: str | None = None,
    *,
    base_url: str | None = None,
    timeout: float = 60.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAIChatModel:
    """Create an `OpenAIChatModel` configured for chat completions."""
    effective_key = (api_key or "").strip() or "missing-openai-api-key"
    kwargs: dict[str, Any] = {
        "model": model_name,
        "api_key": effective_key,
        "timeout": timeout,
        "transport": transport,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAIChatModel(**kwargs)
