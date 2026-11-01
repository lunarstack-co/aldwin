"""OpenAI model discovery, API key verification, and retrying model wrapper."""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from enum import StrEnum
from typing import Any, Final, Literal

import httpx
import openai
from agentling import ChatMessage, Delta, Model, OpenAIModel
from agentling.errors import ModelError
from agentling.models import ToolSpec

from aldwin.core.logging import _redact_text

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


class TransientModelError(ModelError):
    """Retryable model error used by tests and provider adapters."""


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
    client: openai.AsyncOpenAI | None = None,
) -> list[str]:
    """Fetch OpenAI models accessible to `api_key`, filtered to chat models.

    Sorted newest first by `created` timestamp (tie-broken alphabetically by ID).
    """
    cleaned_key = api_key.strip()
    if not cleaned_key:
        raise ValueError("API key must not be empty.")

    owns_client = client is None
    active_client = client or openai.AsyncOpenAI(
        api_key=cleaned_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
    )
    try:
        async with asyncio.timeout(timeout):
            page = await active_client.models.list()
    finally:
        if owns_client:
            await active_client.close()

    raw_items: Sequence[Any] = getattr(page, "data", None) or list(page)
    by_id: dict[str, int] = {}
    for item in raw_items:
        model_id = str(getattr(item, "id", "")).strip()
        if not is_chat_model(model_id):
            continue
        created_raw = getattr(item, "created", 0)
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
    client: openai.AsyncOpenAI | None = None,
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
            client=client,
        )
    except openai.AuthenticationError:
        return KeyStatus.INVALID
    except openai.PermissionDeniedError:
        return KeyStatus.NO_ACCESS
    except (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
        openai.APIStatusError,
        httpx.HTTPError,
        TimeoutError,
    ):
        return KeyStatus.NETWORK_ERROR

    if not models:
        return KeyStatus.NO_ACCESS
    return KeyStatus.OK


def is_retryable_model_error(exc: BaseException) -> bool:
    """Return `True` if `exc` represents a transient OpenAI failure."""
    if isinstance(
        exc,
        (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.BadRequestError,
            openai.NotFoundError,
            openai.UnprocessableEntityError,
        ),
    ):
        return False

    if isinstance(
        exc,
        (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
            TransientModelError,
        ),
    ):
        return True

    if isinstance(exc, openai.APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500

    if isinstance(exc, ModelError):
        text = str(exc).lower()
        return any(
            token in text
            for token in ("timeout", "429", "500", "502", "503", "504", "transient")
        )

    return False


def classify_model_error(
    exc: BaseException,
    *,
    model: str | None = None,
) -> tuple[ModelErrorCode, str]:
    """Map a model or Agentling exception to `(ModelErrorCode, message)`.

    Protocol codes stay stable (`unknown_model`, `model_unavailable`, …); the
    CLI maps them to house copy. Optional `model` names the configured model on
    not-found errors.
    """
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return (
            "auth_failed",
            "Your OpenAI key was rejected. Run `aldwin setup` to update it.",
        )
    if isinstance(exc, openai.NotFoundError):
        name = (model or "").strip() or "that model"
        return (
            "unknown_model",
            f"Model {name} isn't available to your key. Pick another with /model.",
        )
    if isinstance(exc, openai.RateLimitError):
        return (
            "rate_limited",
            "OpenAI rate limit reached. Wait a moment and try again.",
        )
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return (
            "model_unavailable",
            "Can't reach OpenAI. Check your connection, then press ↑ Enter to retry.",
        )
    if isinstance(exc, openai.InternalServerError):
        return (
            "rate_limited",
            "OpenAI is busy right now. Wait a moment and try again.",
        )
    if isinstance(exc, openai.APIStatusError):
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
        if exc.status_code == 404 or "not found" in text:
            name = (model or "").strip() or "that model"
            return (
                "unknown_model",
                f"Model {name} isn't available to your key. Pick another with /model.",
            )
        if exc.status_code >= 500:
            return (
                "rate_limited",
                "OpenAI is busy right now. Wait a moment and try again.",
            )
        too_many = _TOO_MANY_TOOLS_RE.search(str(exc))
        if too_many is not None:
            limit, count = too_many.groups()
            return (
                "request_rejected",
                f"OpenAI allows at most {limit} tools and {count} are switched on. "
                "Turn some off in /connectors.",
            )
        return (
            "request_rejected",
            f"OpenAI refused the request (HTTP {exc.status_code}). "
            "Details are in ~/.aldwin/logs/aldwin.log.",
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
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError)):
        return f"No internet connection. Retrying in {delay_label} {progress}…"
    return f"OpenAI is busy. Retrying in {delay_label} {progress}…"


class RetryingModel(Model):
    """Wraps any Agentling `Model` with exponential-backoff retries.

    Retries up to `max_retries` times (default 3: `1s, 2s, 4s ± jitter`) on rate
    limits, 5xx responses, and connection/timeout errors before any stream chunk
    is yielded. Invokes `on_retry(attempt, max_retries, exc, delay_s)` before each
    sleep, where `delay_s` is the sleep about to run (including jitter).
    """

    def __init__(
        self,
        inner: Model,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        jitter: float = 0.2,
        on_retry: RetryCallback | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0.")
        self.inner = inner
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.jitter = jitter
        self.on_retry = on_retry
        self._sleep = sleep
        self._rng = rng

    def _compute_delay(self, attempt: int) -> float:
        nominal = self.base_delay * float(1 << attempt)
        if self.jitter <= 0:
            return nominal
        factor = self._rng(1.0 - self.jitter, 1.0 + self.jitter)
        return max(0.0, nominal * factor)

    async def _notify_retry(
        self, attempt: int, exc: BaseException, delay_s: float
    ) -> None:
        logger.warning(
            "Model transient error (retrying %d of %d): %s",
            attempt,
            self.max_retries,
            _redact_text(str(exc)),
        )
        if self.on_retry is None:
            return
        result = self.on_retry(attempt, self.max_retries, exc, delay_s)
        if inspect.isawaitable(result):
            await result

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
    ) -> ChatMessage:
        """Generate one response, retrying transient failures up to `max_retries`."""
        for attempt in range(self.max_retries + 1):
            try:
                return await self.inner.generate(messages, tools=tools)
            except Exception as exc:
                if not is_retryable_model_error(exc) or attempt >= self.max_retries:
                    raise
                delay_s = self._compute_delay(attempt)
                await self._notify_retry(attempt + 1, exc, delay_s)
                await self._sleep(delay_s)

        raise RuntimeError("Unexpected retry loop exit.")

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[Delta]:
        """Stream deltas, retrying transient failures before the first chunk."""
        for attempt in range(self.max_retries + 1):
            yielded_any = False
            try:
                async for delta in self.inner.stream(messages, tools=tools):
                    yielded_any = True
                    yield delta
                return
            except Exception as exc:
                if (
                    yielded_any
                    or not is_retryable_model_error(exc)
                    or attempt >= self.max_retries
                ):
                    raise
                delay_s = self._compute_delay(attempt)
                await self._notify_retry(attempt + 1, exc, delay_s)
                await self._sleep(delay_s)


def build_openai_model(
    model_name: str,
    api_key: str | None = None,
    *,
    base_url: str | None = None,
    on_retry: RetryCallback | None = None,
    max_retries: int = 3,
) -> RetryingModel:
    """Create an `OpenAIModel` wrapped in `RetryingModel` with SDK retries disabled."""
    effective_key = (api_key or "").strip() or "missing-openai-api-key"
    inner = OpenAIModel(
        model=model_name,
        api_key=effective_key,
        base_url=base_url,
        max_retries=0,
    )
    inner.client = inner.client.with_options(max_retries=0)
    return RetryingModel(
        inner,
        max_retries=max_retries,
        on_retry=on_retry,
    )
