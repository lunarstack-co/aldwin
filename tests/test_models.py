"""Tests for `aldwin.core.models` and `aldwin.core.providers.openai_chat`."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from agentling import (
    ChatMessage,
    ModelAuthError,
    ModelBadRequestError,
    ModelConnectionError,
    ModelContextLengthError,
    ModelError,
    ModelOutputTruncatedError,
    ModelRateLimitError,
    ModelServerError,
    ModelTimeoutError,
    ToolCall,
    ToolDefinition,
)
from agentling.testing import ScriptedModel
from fakes.model import FakeModel, FakeTurn

from aldwin.core.models import (
    KeyStatus,
    build_openai_model,
    check_key,
    classify_model_error,
    is_chat_model,
    list_chat_models,
    retry_status_message,
)
from aldwin.core.providers.openai_chat import OpenAIChatModel, request_body


def _sse_line(data: dict[str, Any] | str) -> bytes:
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"data: {payload}\n\n".encode("utf-8")


async def test_openai_chat_model_streams_text() -> None:
    """`OpenAIChatModel` streams text completion and finishes with usage."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        assert body["model"] == "gpt-4o"
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}

        chunks = [
            _sse_line({"choices": [{"delta": {"role": "assistant"}}]}),
            _sse_line({"choices": [{"delta": {"content": "Hello "}}]}),
            _sse_line({"choices": [{"delta": {"content": "world"}}]}),
            _sse_line(
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                }
            ),
            _sse_line("[DONE]"),
        ]
        return httpx.Response(
            200,
            content=b"".join(chunks),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    model = OpenAIChatModel("gpt-4o", api_key="sk-test", transport=transport)
    messages = [ChatMessage(role="user", content="hi")]

    deltas = [d async for d in model.stream(messages)]
    assert [d.content for d in deltas if d.content] == ["Hello ", "world"]
    assert any(d.finish_reason == "stop" for d in deltas)
    final_usage = [d.usage for d in deltas if d.usage]
    assert len(final_usage) == 1
    assert final_usage[0].input_tokens == 5
    assert final_usage[0].output_tokens == 2


async def test_openai_chat_model_streams_tool_call() -> None:
    """`OpenAIChatModel` streams tool calls with finish_reason='tool_calls'."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert len(body["tools"]) == 1
        assert body["tools"][0]["function"]["name"] == "web_search"

        chunks = [
            _sse_line(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_123",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query": "cats"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }
            ),
            _sse_line("[DONE]"),
        ]
        return httpx.Response(
            200,
            content=b"".join(chunks),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    model = OpenAIChatModel("gpt-4o", api_key="sk-test", transport=transport)
    tools = [
        ToolDefinition(
            name="web_search",
            description="Search web",
            parameters={"type": "object"},
        )
    ]

    deltas = [
        d
        async for d in model.stream(
            [ChatMessage(role="user", content="search cats")], tools=tools
        )
    ]
    tool_calls = [c for d in deltas for c in d.tool_calls]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "web_search"
    assert tool_calls[0].arguments == '{"query": "cats"}'
    assert any(d.finish_reason == "tool_calls" for d in deltas)


async def test_openai_chat_model_missing_id_and_index() -> None:
    """Missing tool-call ID and index are normalized without crashing the decoder."""

    def handler(_request: httpx.Request) -> httpx.Response:
        chunks = [
            _sse_line(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "type": "function",
                                        "function": {
                                            "name": "calculate",
                                            "arguments": '{"expr": "1+1"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ),
            _sse_line("[DONE]"),
        ]
        return httpx.Response(
            200,
            content=b"".join(chunks),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    model = OpenAIChatModel("gpt-4o", api_key="sk-test", transport=transport)
    deltas = [d async for d in model.stream([ChatMessage(role="user", content="calc")])]
    tool_calls = [c for d in deltas for c in d.tool_calls]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "calculate"
    assert tool_calls[0].index >= 0


async def test_openai_chat_model_http_error_mappings() -> None:
    """`OpenAIChatModel` maps HTTP status codes to Agentling `ModelError` classes."""

    def make_handler(
        status: int,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> httpx.MockTransport:
        resp_headers = {"content-type": "application/json"}
        if headers:
            resp_headers.update(headers)
        return httpx.MockTransport(
            lambda _r: httpx.Response(status, json=body, headers=resp_headers)
        )

    m_401 = OpenAIChatModel(
        "gpt-4o",
        api_key="sk-bad",
        transport=make_handler(401, {"error": {"message": "Invalid key"}}),
    )
    with pytest.raises(ModelAuthError) as exc_401:
        async for _ in m_401.stream([ChatMessage(role="user", content="hi")]):
            pass
    assert exc_401.value.status == 401
    assert exc_401.value.retryable is False

    m_429 = OpenAIChatModel(
        "gpt-4o",
        api_key="sk-test",
        transport=make_handler(
            429,
            {"error": {"message": "Rate limit"}},
            headers={"retry-after": "4.5"},
        ),
    )
    with pytest.raises(ModelRateLimitError) as exc_429:
        async for _ in m_429.stream([ChatMessage(role="user", content="hi")]):
            pass
    assert exc_429.value.status == 429
    assert exc_429.value.retry_after == 4.5
    assert exc_429.value.retryable is True

    m_503 = OpenAIChatModel(
        "gpt-4o",
        api_key="sk-test",
        transport=make_handler(503, {"error": {"message": "Overloaded"}}),
    )
    with pytest.raises(ModelServerError) as exc_503:
        async for _ in m_503.stream([ChatMessage(role="user", content="hi")]):
            pass
    assert exc_503.value.status == 503
    assert exc_503.value.retryable is True

    m_404 = OpenAIChatModel(
        "gpt-invalid",
        api_key="sk-test",
        transport=make_handler(404, {"error": {"message": "Model not found"}}),
    )
    with pytest.raises(ModelBadRequestError) as exc_404:
        async for _ in m_404.stream([ChatMessage(role="user", content="hi")]):
            pass
    assert exc_404.value.status == 404
    assert exc_404.value.retryable is False
    assert (
        classify_model_error(exc_404.value, model="gpt-invalid")[0] == "unknown_model"
    )

    m_ctx = OpenAIChatModel(
        "gpt-4o",
        api_key="sk-test",
        transport=make_handler(
            400,
            {"error": {"message": "context_length_exceeded: maximum is 128k"}},
        ),
    )
    with pytest.raises(ModelContextLengthError) as exc_ctx:
        async for _ in m_ctx.stream([ChatMessage(role="user", content="hi")]):
            pass
    assert exc_ctx.value.status == 400
    assert exc_ctx.value.retryable is False

    m_tools = OpenAIChatModel(
        "gpt-4o",
        api_key="sk-test",
        transport=make_handler(
            400,
            {
                "error": {
                    "message": (
                        "Invalid 'tools': array too long. Expected an array with "
                        "maximum length 128, but got an array with length 140 instead."
                    )
                }
            },
        ),
    )
    with pytest.raises(ModelBadRequestError) as exc_tools:
        async for _ in m_tools.stream([ChatMessage(role="user", content="hi")]):
            pass
    code_tools, msg_tools = classify_model_error(exc_tools.value)
    assert code_tools == "request_rejected"
    assert "at most 128 tools and 140 are switched on" in msg_tools

    m_400 = OpenAIChatModel(
        "gpt-4o",
        api_key="sk-test",
        transport=make_handler(400, {"error": {"message": "Bad parameter"}}),
    )
    with pytest.raises(ModelBadRequestError) as exc_400:
        async for _ in m_400.stream([ChatMessage(role="user", content="hi")]):
            pass
    assert exc_400.value.status == 400


async def test_openai_chat_model_network_and_stream_errors() -> None:
    """Network timeouts, connection drops, and mid-stream SSE errors map cleanly."""

    def timeout_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    m_timeout = OpenAIChatModel(
        "gpt-4o", api_key="sk-test", transport=httpx.MockTransport(timeout_handler)
    )
    with pytest.raises(ModelTimeoutError):
        async for _ in m_timeout.stream([ChatMessage(role="user", content="hi")]):
            pass

    def conn_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    m_conn = OpenAIChatModel(
        "gpt-4o", api_key="sk-test", transport=httpx.MockTransport(conn_handler)
    )
    with pytest.raises(ModelConnectionError):
        async for _ in m_conn.stream([ChatMessage(role="user", content="hi")]):
            pass

    def stream_err_handler(_request: httpx.Request) -> httpx.Response:
        chunks = [
            _sse_line({"error": {"message": "Internal stream crash", "code": 500}}),
        ]
        return httpx.Response(
            200,
            content=b"".join(chunks),
            headers={"content-type": "text/event-stream"},
        )

    m_stream_err = OpenAIChatModel(
        "gpt-4o", api_key="sk-test", transport=httpx.MockTransport(stream_err_handler)
    )
    with pytest.raises(ModelServerError):
        async for _ in m_stream_err.stream([ChatMessage(role="user", content="hi")]):
            pass


async def test_list_chat_models_and_check_key() -> None:
    """`list_chat_models` and `check_key` validate keys and sort chat models."""
    with pytest.raises(ValueError, match="API key must not be empty"):
        await list_chat_models("")

    models_data = {
        "data": [
            {"id": "gpt-4o", "created": 200},
            {"id": "gpt-4.1", "created": 300},
            {"id": "o3-mini", "created": 250},
            {"id": "text-embedding-3-small", "created": 400},
            {"id": "whisper-1", "created": 100},
        ]
    }
    t_ok = httpx.MockTransport(lambda _r: httpx.Response(200, json=models_data))
    models = await list_chat_models("sk-test", transport=t_ok)
    assert models == ["gpt-4.1", "o3-mini", "gpt-4o"]
    assert is_chat_model("gpt-4o") is True
    assert is_chat_model("text-embedding-3-small") is False

    async with httpx.AsyncClient(transport=t_ok) as client:
        client_models = await list_chat_models("sk-test", client=client)
        assert client_models == ["gpt-4.1", "o3-mini", "gpt-4o"]
        assert await check_key("sk-test", client=client) == KeyStatus.OK

    assert await check_key("sk-test", transport=t_ok) == KeyStatus.OK

    assert await check_key("") == KeyStatus.INVALID
    t_401 = httpx.MockTransport(
        lambda _r: httpx.Response(401, json={"error": {"message": "Invalid"}})
    )
    assert await check_key("sk-test", transport=t_401) == KeyStatus.INVALID

    t_403 = httpx.MockTransport(
        lambda _r: httpx.Response(403, json={"error": {"message": "Forbidden"}})
    )
    assert await check_key("sk-test", transport=t_403) == KeyStatus.NO_ACCESS

    t_empty = httpx.MockTransport(
        lambda _r: httpx.Response(
            200, json={"data": [{"id": "whisper-1", "created": 1}]}
        )
    )
    assert await check_key("sk-test", transport=t_empty) == KeyStatus.NO_ACCESS

    t_500 = httpx.MockTransport(
        lambda _r: httpx.Response(500, json={"error": {"message": "Busy"}})
    )
    assert await check_key("sk-test", transport=t_500) == KeyStatus.NETWORK_ERROR


def test_classify_model_error() -> None:
    """`classify_model_error` maps Agentling `ModelError` classes to protocol codes."""
    code, msg = classify_model_error(ModelAuthError("bad key"))
    assert code == "auth_failed"
    assert "aldwin setup" in msg

    code, _ = classify_model_error(ModelRateLimitError("rate limit"))
    assert code == "rate_limited"
    code, msg = classify_model_error(ModelServerError("server error"))
    assert code == "rate_limited"
    assert "returned a server error" in msg.lower()

    code, _ = classify_model_error(ModelConnectionError("connection failed"))
    assert code == "model_unavailable"
    code, _ = classify_model_error(ModelTimeoutError("timed out"))
    assert code == "model_unavailable"

    code, msg = classify_model_error(
        ModelBadRequestError("Model not found", status=404), model="gpt-custom"
    )
    assert code == "unknown_model"
    assert "gpt-custom" in msg
    assert "/model" in msg

    code, msg = classify_model_error(
        ModelBadRequestError(
            "'tools': maximum length 128, but got an array with length 130"
        )
    )
    assert code == "request_rejected"
    assert "at most 128 tools" in msg
    assert "/connectors" in msg

    code, _ = classify_model_error(ModelContextLengthError("context length exceeded"))
    assert code == "request_rejected"
    code, _ = classify_model_error(ModelOutputTruncatedError("cut off"))
    assert code == "request_rejected"

    code, _ = classify_model_error(ModelError("random internal failure"))
    assert code == "model_unavailable"

    code, msg = classify_model_error(RuntimeError("crash"))
    assert code == "internal"
    assert "Something went wrong" in msg


def test_retry_status_message() -> None:
    """`retry_status_message` distinguishes connection drops from server errors."""
    msg_conn = retry_status_message(
        ModelConnectionError("connect failed"), attempt=1, max_retries=3, delay_s=1.0
    )
    assert "Can't reach OpenAI" in msg_conn
    assert "Retrying in 1s (1/3)…" in msg_conn

    msg_rate = retry_status_message(
        ModelRateLimitError("busy"), attempt=2, max_retries=3, delay_s=2.0
    )
    assert "OpenAI is busy" in msg_rate
    assert "retrying in 2s (2/3)…" in msg_rate


def test_build_openai_model_helper() -> None:
    """`build_openai_model` constructs `OpenAIChatModel` with parameters."""
    model = build_openai_model(
        "gpt-4o", "sk-test", base_url="https://api.openai.com/v1"
    )
    assert isinstance(model, OpenAIChatModel)
    assert model.model == "gpt-4o"
    assert model.api_key == "sk-test"
    assert model.base_url == "https://api.openai.com/v1"


def test_request_body_formatting() -> None:
    """`request_body` constructs OpenAI chat completions payload."""
    messages = [
        ChatMessage(role="system", content="Be helpful"),
        ChatMessage(role="user", content="hello"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="t1", arguments={"a": 1})],
        ),
        ChatMessage(role="tool", content="ok", tool_call_id="c1"),
    ]
    tools = [
        ToolDefinition(name="t1", description="desc", parameters={"type": "object"})
    ]
    body = request_body("gpt-4o", messages, tools)
    assert body["model"] == "gpt-4o"
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}
    assert len(body["messages"]) == 4
    assert body["messages"][2]["tool_calls"][0]["id"] == "c1"
    assert body["messages"][3]["tool_call_id"] == "c1"
    assert len(body["tools"]) == 1


async def test_fake_model_wraps_scripted_model() -> None:
    """`FakeModel` subclasses `ScriptedModel` and delegates turn replay."""
    hang_turn = FakeTurn.hang(prefix_chunks=["wait"])
    fake = FakeModel(
        turns=[
            FakeTurn.text(["hello ", "there"]),
            FakeTurn.tool_call("calculate", {"expr": "2+2"}, call_id="c_calc"),
            FakeTurn.error(ModelRateLimitError("rate limited")),
            hang_turn,
        ]
    )
    assert isinstance(fake, ScriptedModel)

    msg = [ChatMessage(role="user", content="hi")]
    tool_defs = [
        ToolDefinition(
            name="calculate", description="calc", parameters={"type": "object"}
        )
    ]

    d1 = [d async for d in fake.stream(msg, tools=tool_defs)]
    assert [d.content for d in d1 if d.content] == ["hello ", "there"]
    assert len(fake._turns) == 1
    assert fake.received_messages == msg

    d2 = [d async for d in fake.stream(msg, tools=tool_defs)]
    assert any(d.finish_reason == "tool_calls" for d in d2)
    assert len(fake._turns) == 2

    with pytest.raises(ModelRateLimitError):
        async for _ in fake.stream(msg, tools=tool_defs):
            pass
    assert len(fake._turns) == 3

    streamed_hang: list[str] = []

    async def consume_hang() -> None:
        async for d in fake.stream(msg, tools=tool_defs):
            if d.content:
                streamed_hang.append(d.content)

    task = asyncio.create_task(consume_hang())
    await asyncio.wait_for(hang_turn.hang_started.wait(), timeout=1.0)
    assert streamed_hang == ["wait"]
    hang_turn.hang_release.set()
    await asyncio.wait_for(task, timeout=1.0)
