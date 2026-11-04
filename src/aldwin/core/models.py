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
from aldwin.core.providers.openai_chat import OpenAIChatModel, error_for_response

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

_CHAT_MODEL_PREFIXES: Final[tuple[str, ...]] = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "o1",
    "o3",
    "o4",
    "chatgpt-",
)

_NON_CHAT_MARKERS: Final[tuple[str, ...]] = (
    "embedding",
    "whisper",
    "tts",
    "dall-e",
    "moderation",
    "realtime",
    "audio",
    "transcribe",
    "search",
    "image",
    "instruct",
    "babbage",
    "davinci",
)

RetryCallback = Callable[[int, int, BaseException, float], Awaitable[None] | None]


class KeyStatus(StrEnum):
    """Result of validating an OpenAI API key against the models endpoint."""

    OK = "ok"
    INVALID = "invalid"
    NO_ACCESS = "no_access"
    NETWORK_ERROR = "network_error"


def is_chat_model(model_id: str) -> bool:
    """Return `True` if `model_id` is an OpenAI chat-completions model."""
    lowered = model_id.strip().lower()
    if not lowered or not lowered.startswith(_CHAT_MODEL_PREFIXES):
        return False
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


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
    headers = {"Authorization": f"Bearer {cleaned_key}"}

    owns_client = client is None
    active_client = client or httpx.AsyncClient(transport=transport, timeout=timeout)
    try:
        try:
            async with asyncio.timeout(timeout):
                response = await active_client.get(
                    f"{root_url}/models", headers=headers
                )
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

    finally:
        if owns_client:
            await active_client.aclose()

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
) -> tuple[ModelErrorCode, str]:
    """Map an Agentling ModelError to `(ModelErrorCode, message)`.

    Protocol codes stay stable (`unknown_model`, `model_unavailable`, …); the
    CLI maps them to house copy. Optional `model` names the configured model on
    not-found errors.
    """
    if isinstance(exc, ModelAuthError):
        return (
            "auth_failed",
            "Your OpenAI key was rejected. Run `aldwin setup` to update it.",
        )
    if isinstance(exc, ModelRateLimitError):
        return (
            "rate_limited",
            "OpenAI rate limit reached. Wait a moment and try again.",
        )
    if isinstance(exc, ModelServerError):
        return (
            "rate_limited",
            "OpenAI is busy right now. Wait a moment and try again.",
        )
    if isinstance(exc, (ModelConnectionError, ModelTimeoutError)):
        return (
            "model_unavailable",
            "Can't reach OpenAI. Check your connection, then press ↑ Enter to retry.",
        )
    if isinstance(exc, ModelBadRequestError):
        if (
            exc.status == 404
            or "404" in str(exc).lower()
            or "not found" in str(exc).lower()
        ):
            name = (model or "").strip() or "that model"
            return (
                "unknown_model",
                f"Model {name} isn't available to your key. Pick another with /model.",
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
            f"OpenAI refused the request ({status_label}). "
            "Details are in ~/.aldwin/logs/aldwin.log.",
        )
    if isinstance(exc, (ModelContextLengthError, ModelOutputTruncatedError)):
        return (
            "request_rejected",
            "The request was rejected because the model's context or length "
            "limit was exceeded. Details are in ~/.aldwin/logs/aldwin.log.",
        )
    if isinstance(exc, ModelError):
        text = _redact_text(str(exc)).lower()
        if "401" in text or "authentication" in text or "api key" in text:
            return (
                "auth_failed",
                "Your OpenAI key was rejected. Run `aldwin setup` to update it.",
            )
        if "429" in text or "rate limit" in text:
            return (
                "rate_limited",
                "OpenAI rate limit reached. Wait a moment and try again.",
            )
        if "404" in text or "not found" in text:
            name = (model or "").strip() or "that model"
            return (
                "unknown_model",
                f"Model {name} isn't available to your key. Pick another with /model.",
            )
        return (
            "model_unavailable",
            "Can't reach OpenAI. Check your connection, then press ↑ Enter to retry.",
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
) -> str:
    """User-facing retry line for a transient model error (host → StatusEvent)."""
    delay_label = f"{int(delay_s)}s" if delay_s >= 1 else f"{delay_s:.1f}s"
    progress = f"({attempt}/{max_retries})"
    if isinstance(exc, (ModelConnectionError, ModelTimeoutError)):
        return f"No internet connection. Retrying in {delay_label} {progress}…"
    return f"OpenAI is busy. Retrying in {delay_label} {progress}…"


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
