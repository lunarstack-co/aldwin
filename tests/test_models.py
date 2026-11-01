"""Tests for `aldwin.core.models` (`check_key`, `list_chat_models`, `RetryingModel`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import httpx
import openai
import pytest
from agentling import ChatMessage, Delta
from agentling.errors import ModelError
from fakes.model import FakeModel, FakeTurn

from aldwin.core.models import (
    KeyStatus,
    RetryingModel,
    TransientModelError,
    build_openai_model,
    check_key,
    classify_model_error,
    is_chat_model,
    is_retryable_model_error,
    list_chat_models,
    retry_status_message,
)


@dataclass
class _StubModelEntry:
    id: str
    created: int


@dataclass
class _StubModelPage:
    data: list[_StubModelEntry]


class _StubModelsAPI:
    def __init__(self, outcome: _StubModelPage | Exception) -> None:
        self._outcome = outcome

    async def list(self) -> _StubModelPage:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _StubOpenAIClient:
    def __init__(self, outcome: _StubModelPage | Exception) -> None:
        self.models = _StubModelsAPI(outcome)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _http_response(status_code: int) -> httpx.Response:
    req = httpx.Request("GET", "https://api.openai.com/v1/models")
    return httpx.Response(status_code, request=req)


async def test_check_key_statuses() -> None:
    """`check_key` returns `invalid`, `no_access`, `network_error`, and `ok`."""
    assert await check_key("") == KeyStatus.INVALID
    assert await check_key("   ") == KeyStatus.INVALID

    err_401 = openai.AuthenticationError(
        "Incorrect API key provided: sk-secret1234567890",
        response=cast(Any, _http_response(401)),
        body=None,
    )
    client_401 = _StubOpenAIClient(err_401)
    assert await check_key("sk-test", client=cast(Any, client_401)) == KeyStatus.INVALID

    err_403 = openai.PermissionDeniedError(
        "Project has no access",
        response=cast(Any, _http_response(403)),
        body=None,
    )
    client_403 = _StubOpenAIClient(err_403)
    assert (
        await check_key("sk-test", client=cast(Any, client_403)) == KeyStatus.NO_ACCESS
    )

    client_no_chat = _StubOpenAIClient(
        _StubModelPage(data=[_StubModelEntry(id="text-embedding-3-small", created=10)])
    )
    assert (
        await check_key("sk-test", client=cast(Any, client_no_chat))
        == KeyStatus.NO_ACCESS
    )

    req = httpx.Request("GET", "https://api.openai.com/v1/models")
    err_net = openai.APIConnectionError(request=cast(Any, req))
    client_net = _StubOpenAIClient(err_net)
    assert (
        await check_key("sk-test", client=cast(Any, client_net))
        == KeyStatus.NETWORK_ERROR
    )

    client_timeout = _StubOpenAIClient(TimeoutError("timed out"))
    assert (
        await check_key("sk-test", client=cast(Any, client_timeout))
        == KeyStatus.NETWORK_ERROR
    )

    client_ok = _StubOpenAIClient(
        _StubModelPage(data=[_StubModelEntry(id="gpt-4o", created=200)])
    )
    assert await check_key("sk-test", client=cast(Any, client_ok)) == KeyStatus.OK


async def test_list_chat_models_filters_non_chat_and_sorts_newest_first() -> None:
    """`list_chat_models` excludes non-chat models and sorts newest first."""
    page = _StubModelPage(
        data=[
            _StubModelEntry(id="gpt-4o", created=200),
            _StubModelEntry(id="gpt-4.1", created=300),
            _StubModelEntry(id="o3-mini", created=250),
            _StubModelEntry(id="text-embedding-3-large", created=400),
            _StubModelEntry(id="whisper-1", created=100),
            _StubModelEntry(id="dall-e-3", created=350),
            _StubModelEntry(id="gpt-4o-realtime-preview", created=360),
            _StubModelEntry(id="gpt-4o-audio-preview", created=370),
            _StubModelEntry(id="gpt-4o-transcribe", created=380),
            _StubModelEntry(id="omni-moderation-latest", created=390),
        ]
    )
    stub_client = _StubOpenAIClient(page)
    models = await list_chat_models("sk-test", client=cast(Any, stub_client))

    assert models == ["gpt-4.1", "o3-mini", "gpt-4o"]
    assert is_chat_model("gpt-4o") is True
    assert is_chat_model("gpt-4.1-mini") is True
    assert is_chat_model("o4-mini") is True
    assert is_chat_model("text-embedding-3-small") is False
    assert is_chat_model("gpt-4o-realtime-preview") is False
    assert is_chat_model("") is False


async def test_retrying_model_retries_429_twice_then_succeeds() -> None:
    """Model call failing twice with 429 emits 2 retry statuses then succeeds."""
    err_429 = openai.RateLimitError(
        "Rate limit reached",
        response=cast(Any, _http_response(429)),
        body=None,
    )
    fake = FakeModel(
        turns=[
            FakeTurn.error(err_429),
            FakeTurn.error(err_429),
            FakeTurn.text(["Hello ", "world"]),
        ]
    )
    recorded_retries: list[tuple[int, int, type[BaseException], float]] = []
    recorded_sleeps: list[float] = []

    async def on_retry(
        attempt: int, max_retries: int, exc: BaseException, delay_s: float
    ) -> None:
        recorded_retries.append((attempt, max_retries, type(exc), delay_s))

    async def fake_sleep(delay: float) -> None:
        recorded_sleeps.append(delay)

    model = RetryingModel(
        fake,
        max_retries=3,
        base_delay=1.0,
        jitter=0.2,
        on_retry=on_retry,
        sleep=fake_sleep,
    )

    deltas: list[Delta] = []
    async for delta in model.stream([ChatMessage(role="user", content="hi")]):
        deltas.append(delta)

    assert [d.content for d in deltas if d.content] == ["Hello ", "world"]
    assert len(fake.calls) == 3
    assert len(recorded_retries) == 2
    assert recorded_retries[0][:3] == (1, 3, openai.RateLimitError)
    assert recorded_retries[1][:3] == (2, 3, openai.RateLimitError)
    assert len(recorded_sleeps) == 2
    assert 0.8 <= recorded_sleeps[0] <= 1.2
    assert 1.6 <= recorded_sleeps[1] <= 2.4
    assert recorded_retries[0][3] == recorded_sleeps[0]
    assert recorded_retries[1][3] == recorded_sleeps[1]


async def test_retrying_model_exhausts_3_retries_on_503() -> None:
    """Model failing 4 times (initial + 3 retries) with 503 raises on 4th."""
    err_503 = openai.InternalServerError(
        "Service unavailable",
        response=cast(Any, _http_response(503)),
        body=None,
    )
    fake = FakeModel(
        turns=[
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
        ]
    )
    retry_attempts: list[tuple[int, int]] = []
    sleeps: list[float] = []

    def on_retry(
        attempt: int, max_retries: int, _exc: BaseException, _delay_s: float
    ) -> None:
        retry_attempts.append((attempt, max_retries))

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    model = RetryingModel(
        fake,
        max_retries=3,
        base_delay=1.0,
        jitter=0.0,
        on_retry=on_retry,
        sleep=fake_sleep,
    )

    with pytest.raises(openai.InternalServerError):
        async for _ in model.stream([ChatMessage(role="user", content="hi")]):
            pass

    assert len(fake.calls) == 4
    assert retry_attempts == [(1, 3), (2, 3), (3, 3)]
    assert sleeps == [1.0, 2.0, 4.0]
    code, msg = classify_model_error(err_503)
    assert code == "rate_limited"
    assert "busy" in msg.lower()


async def test_retrying_model_never_retries_auth_or_mid_stream_failure() -> None:
    """Auth errors (401) and mid-stream failures after a chunk do not retry."""
    err_401 = openai.AuthenticationError(
        "Invalid key sk-proj-secretkeyvalue123456",
        response=cast(Any, _http_response(401)),
        body=None,
    )
    fake_auth = FakeModel(turns=[FakeTurn.error(err_401)])
    retries_called = 0

    def on_retry(
        _attempt: int, _max: int, _exc: BaseException, _delay_s: float
    ) -> None:
        nonlocal retries_called
        retries_called += 1

    async def fake_sleep(_delay: float) -> None:
        pass

    model_auth = RetryingModel(fake_auth, on_retry=on_retry, sleep=fake_sleep)
    with pytest.raises(openai.AuthenticationError):
        await model_auth.generate([ChatMessage(role="user", content="hi")])

    assert retries_called == 0
    assert len(fake_auth.calls) == 1
    code, message = classify_model_error(err_401)
    assert code == "auth_failed"
    assert "sk-proj-secretkeyvalue123456" not in message

    err_404 = openai.NotFoundError(
        "The model `gpt-does-not-exist` does not exist",
        response=cast(Any, _http_response(404)),
        body=None,
    )
    code_404, msg_404 = classify_model_error(err_404, model="gpt-does-not-exist")
    assert code_404 == "unknown_model"
    assert "gpt-does-not-exist" in msg_404
    assert "/model" in msg_404

    # Mid-stream failure after yielding one Delta must not retry from scratch.
    class _MidStreamFailingModel(FakeModel):
        async def stream(self, messages: Any, tools: Any = None) -> Any:
            self.calls.append(list(messages))
            yield Delta(content="Part 1")
            raise TransientModelError("connection dropped mid-stream")

    mid_fake = _MidStreamFailingModel()
    model_mid = RetryingModel(mid_fake, on_retry=on_retry, sleep=fake_sleep)
    received: list[str] = []
    with pytest.raises(TransientModelError):
        async for delta in model_mid.stream([ChatMessage(role="user", content="hi")]):
            if delta.content:
                received.append(delta.content)

    assert received == ["Part 1"]
    assert retries_called == 0
    assert len(mid_fake.calls) == 1


def test_openai_model_disables_inner_sdk_retries() -> None:
    """`build_openai_model` disables inner `OpenAIModel` and SDK retries."""
    wrapped = build_openai_model("gpt-4o", "sk-test-key-12345678")
    assert wrapped.max_retries == 3
    inner = cast(Any, wrapped.inner)
    assert inner.max_retries == 0
    assert inner.client.max_retries == 0


def test_classify_api_status_error_with_model_word_is_not_unknown_model() -> None:
    """A 400 whose body mentions 'model' must not map to unknown_model."""
    err = openai.APIStatusError(
        "Invalid model parameter for this endpoint",
        response=cast(Any, _http_response(400)),
        body=None,
    )
    code, msg = classify_model_error(err, model="gpt-4o")
    assert code == "request_rejected"
    assert "HTTP 400" in msg and "/model" not in msg
    assert "connection" not in msg


def test_classify_too_many_tools_says_what_to_turn_off() -> None:
    err = openai.BadRequestError(
        "Error code: 400 - {'error': {'message': \"Invalid 'tools': array too long. "
        "Expected an array with maximum length 128, but got an array with length "
        '161 instead."}}',
        response=cast(Any, _http_response(400)),
        body=None,
    )
    code, msg = classify_model_error(err)
    assert code == "request_rejected"
    assert msg == (
        "OpenAI allows at most 128 tools and 161 are switched on. "
        "Turn some off in /connectors."
    )


def test_retry_status_message_connection_vs_busy() -> None:
    """Connection failures say no internet; other transients say busy."""
    conn = openai.APIConnectionError(
        request=cast(Any, httpx.Request("GET", "https://api.openai.com")),
    )
    assert "No internet connection" in retry_status_message(
        conn, attempt=1, max_retries=3, delay_s=1.0
    )
    busy = openai.InternalServerError(
        "Service unavailable",
        response=cast(Any, _http_response(503)),
        body=None,
    )
    line = retry_status_message(busy, attempt=2, max_retries=3, delay_s=2.0)
    assert "OpenAI is busy" in line
    assert "(2/3)" in line
    assert "2s" in line


def test_is_retryable_covers_status_and_model_error() -> None:
    """APIStatusError and ModelError retry heuristics match status / tokens."""
    assert is_retryable_model_error(
        openai.APIStatusError("x", response=cast(Any, _http_response(429)), body=None)
    )
    assert is_retryable_model_error(
        openai.APIStatusError("x", response=cast(Any, _http_response(502)), body=None)
    )
    assert not is_retryable_model_error(
        openai.APIStatusError("x", response=cast(Any, _http_response(400)), body=None)
    )
    assert is_retryable_model_error(ModelError("transient upstream timeout"))
    assert not is_retryable_model_error(ModelError("permanent failure"))
    assert not is_retryable_model_error(ValueError("nope"))


def test_classify_api_status_and_model_error_branches() -> None:
    """Cover auth, rate limit, 404, 5xx, and ModelError mappings."""
    auth = openai.APIStatusError(
        "authentication failed for api key",
        response=cast(Any, _http_response(401)),
        body=None,
    )
    assert classify_model_error(auth)[0] == "auth_failed"

    rate = openai.APIStatusError(
        "rate limit exceeded",
        response=cast(Any, _http_response(429)),
        body=None,
    )
    assert classify_model_error(rate)[0] == "rate_limited"

    missing = openai.APIStatusError(
        "model not found",
        response=cast(Any, _http_response(404)),
        body=None,
    )
    code_404, msg_404 = classify_model_error(missing, model="ghost")
    assert code_404 == "unknown_model"
    assert "ghost" in msg_404

    server = openai.APIStatusError(
        "bad gateway",
        response=cast(Any, _http_response(502)),
        body=None,
    )
    assert classify_model_error(server)[0] == "rate_limited"

    assert classify_model_error(ModelError("401 authentication"))[0] == "auth_failed"
    assert classify_model_error(ModelError("429 rate limit"))[0] == "rate_limited"
    code_me, msg_me = classify_model_error(ModelError("404 not found"), model="ghost-2")
    assert code_me == "unknown_model"
    assert "ghost-2" in msg_me
    assert classify_model_error(ModelError("other"))[0] == "model_unavailable"
    assert classify_model_error(RuntimeError("boom"))[0] == "internal"
