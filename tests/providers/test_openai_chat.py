"""Unit tests for OpenAIChatModel and OpenAIChatBackend."""

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
    ModelRateLimitError,
    ModelServerError,
    ToolDefinition,
    agglomerate_deltas,
)

from aldwin.core.providers.base import AccessStatus
from aldwin.core.providers.openai_chat import (
    _BASE_URLS_WITHOUT_STREAM_OPTIONS,
    OpenAIChatBackend,
    OpenAIChatModel,
)
from aldwin.core.providers.registry import ProviderEntry

_USER = [ChatMessage(role="user", content="hi")]

_ECHO_TOOL = ToolDefinition(
    name="echo",
    description="Echo text back.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
)


def _sse(chunks: list[dict[str, Any]]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode("utf-8")


def sse_transport(
    chunks: list[dict[str, Any]],
    status: int = 200,
    headers: dict[str, str] | None = None,
    body: str | None = None,
    requests: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        content = body.encode("utf-8") if body is not None else _sse(chunks)
        return httpx.Response(status, headers=headers, content=content)

    return httpx.MockTransport(handler)


async def test_openai_chat_streams_text_and_usage() -> None:
    chunks: list[dict[str, Any]] = [
        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hel"}}]},
        {"choices": [{"index": 0, "delta": {"content": "lo"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    ]
    model = OpenAIChatModel(
        "gpt-4o", api_key="test-key", transport=sse_transport(chunks)
    )
    deltas = [d async for d in model.stream(_USER)]
    msg = agglomerate_deltas(deltas)
    assert msg.content == "Hello"
    assert msg.finish_reason == "stop"
    assert msg.usage is not None
    assert msg.usage.input_tokens == 10
    assert msg.usage.output_tokens == 5


async def test_openai_chat_streams_tool_call_fragments() -> None:
    chunks = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_123",
                                "function": {"name": "echo", "arguments": '{"text": '},
                            }
                        ],
                    },
                }
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": '"hello"}'}}
                        ],
                    },
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    model = OpenAIChatModel(
        "gpt-4o", api_key="test-key", transport=sse_transport(chunks)
    )
    deltas = [d async for d in model.stream(_USER, tools=[_ECHO_TOOL])]
    msg = agglomerate_deltas(deltas)
    assert msg.finish_reason == "tool_calls"
    assert len(msg.tool_calls) == 1
    call = msg.tool_calls[0]
    assert call.id == "call_123"
    assert call.name == "echo"
    assert call.arguments == {"text": "hello"}


async def test_openai_chat_streams_reasoning_content() -> None:
    chunks = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "thinking..."}}]},
        {"choices": [{"index": 0, "delta": {"content": "answer"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    model = OpenAIChatModel(
        "deepseek-chat", api_key="test-key", transport=sse_transport(chunks)
    )
    deltas = [d async for d in model.stream(_USER)]
    msg = agglomerate_deltas(deltas)
    assert msg.reasoning == "thinking..."
    assert msg.content == "answer"


async def test_openai_chat_stream_options_recovery_and_cache() -> None:
    test_base_url = "https://custom-proxy.internal/v1"
    _BASE_URLS_WITHOUT_STREAM_OPTIONS.discard(test_base_url)

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        req_body = json.loads(request.content)
        if "stream_options" in req_body:
            return httpx.Response(
                400,
                json={"error": {"message": "unknown parameter: stream_options"}},
            )
        return httpx.Response(
            200,
            content=_sse(
                [
                    {"choices": [{"index": 0, "delta": {"content": "ok"}}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]
            ),
        )

    transport = httpx.MockTransport(handler)
    model = OpenAIChatModel(
        "custom-model",
        base_url=test_base_url,
        api_key="key",
        transport=transport,
    )

    # First turn: encounters 400, retries without stream_options, succeeds
    deltas = [d async for d in model.stream(_USER)]
    msg = agglomerate_deltas(deltas)
    assert msg.content == "ok"
    assert len(requests) == 2
    assert "stream_options" in json.loads(requests[0].content)
    assert "stream_options" not in json.loads(requests[1].content)
    assert test_base_url in _BASE_URLS_WITHOUT_STREAM_OPTIONS

    # Second turn: omits stream_options on first try without getting 400
    deltas2 = [d async for d in model.stream(_USER)]
    msg2 = agglomerate_deltas(deltas2)
    assert msg2.content == "ok"
    assert len(requests) == 3
    assert "stream_options" not in json.loads(requests[2].content)


async def test_openrouter_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=_sse(
                [
                    {"choices": [{"index": 0, "delta": {"content": "pong"}}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]
            ),
        )

    model = OpenAIChatModel(
        "anthropic/claude-3-7-sonnet",
        base_url="https://openrouter.ai/api/v1",
        api_key="or-key",
        transport=httpx.MockTransport(handler),
    )
    _ = [d async for d in model.stream(_USER)]
    assert len(requests) == 1
    req = requests[0]
    assert req.headers["HTTP-Referer"] == "https://askaldwin.com"
    assert req.headers["X-Title"] == "Aldwin"


async def test_openai_chat_error_mapping() -> None:
    for status, exc_type in [
        (401, ModelAuthError),
        (429, ModelRateLimitError),
        (404, ModelBadRequestError),
        (500, ModelServerError),
    ]:
        transport = httpx.MockTransport(
            lambda r, s=status: httpx.Response(s, json={"error": {"message": "failed"}})
        )
        model = OpenAIChatModel("gpt-4o", api_key="k", transport=transport)
        with pytest.raises(exc_type):
            _ = [d async for d in model.stream(_USER)]

    # 400 context length exceeded
    ctx_transport = httpx.MockTransport(
        lambda r: httpx.Response(
            400,
            json={"error": {"message": "context_length_exceeded"}},
        )
    )
    model_ctx = OpenAIChatModel("gpt-4o", api_key="k", transport=ctx_transport)
    with pytest.raises(ModelContextLengthError):
        _ = [d async for d in model_ctx.stream(_USER)]


async def test_gemini_api_key_invalid_mapping() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(
            400,
            text="API_KEY_INVALID: The provided API key is not valid.",
        )
    )
    model = OpenAIChatModel(
        "gemini-2.5-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key="bad-key",
        transport=transport,
    )
    with pytest.raises(ModelAuthError) as exc_info:
        _ = [d async for d in model.stream(_USER)]
    assert exc_info.value.status == 400


async def test_local_connection_refused_sets_local_true() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    model = OpenAIChatModel(
        "qwen",
        base_url="http://127.0.0.1:1234/v1",
        transport=httpx.MockTransport(handler),
        local=True,
    )
    with pytest.raises(ModelConnectionError) as exc_info:
        _ = [d async for d in model.stream(_USER)]
    assert getattr(exc_info.value, "local", False) is True


async def test_remote_connection_error_sets_local_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Network unreachable")

    model = OpenAIChatModel(
        "gpt-4o",
        base_url="https://api.openai.com/v1",
        transport=httpx.MockTransport(handler),
        local=False,
    )
    with pytest.raises(ModelConnectionError) as exc_info:
        _ = [d async for d in model.stream(_USER)]
    assert getattr(exc_info.value, "local", False) is False


async def test_list_models_filtering() -> None:
    data = {
        "data": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "text-embedding-3-small"},
            {"id": "dall-e-3"},
            {"id": "chatgpt-4o-latest"},
        ]
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=data))
    entry = ProviderEntry(
        name="openai",
        title="OpenAI",
        shape="openai_chat",
        base_url="https://api.openai.com/v1",
        secret="openai_api_key",
        local=False,
        docs_url="",
    )
    backend = OpenAIChatBackend(entry, api_key="k", transport=transport)
    models = await backend.list_models()
    ids = [m.id for m in models]
    assert "gpt-4o" in ids
    assert "chatgpt-4o-latest" in ids
    assert "text-embedding-3-small" not in ids
    assert "dall-e-3" not in ids


async def test_lmstudio_tool_capability_parsing() -> None:
    data = {
        "data": [
            {
                "id": "qwen2.5-coder",
                "capabilities": {"trained_for_tool_use": True},
            },
            {
                "id": "deepseek-r1",
                "capabilities": {"trained_for_tool_use": False},
            },
        ]
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=data))
    entry = ProviderEntry(
        name="lmstudio",
        title="LM Studio",
        shape="openai_chat",
        base_url="http://localhost:1234/v1",
        secret=None,
        local=True,
        docs_url="",
    )
    backend = OpenAIChatBackend(entry, transport=transport)
    models = await backend.list_models()
    assert len(models) == 2
    qwen = next(m for m in models if m.id == "qwen2.5-coder")
    deepseek = next(m for m in models if m.id == "deepseek-r1")
    assert qwen.supports_tools is True
    assert deepseek.supports_tools is False
    assert qwen.local is True


async def test_openai_chat_check_access_and_describe() -> None:
    data = {"data": [{"id": "gpt-4o"}]}
    entry = ProviderEntry(
        name="openai",
        title="OpenAI",
        shape="openai_chat",
        base_url="https://api.openai.com/v1",
        secret="openai_api_key",
        local=False,
        docs_url="",
    )
    backend = OpenAIChatBackend(
        entry,
        api_key="k",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data)),
    )
    assert await backend.check_access() == AccessStatus.OK
    described = await backend.describe_model("gpt-4o")
    assert described is not None
    assert described.id == "gpt-4o"

    # Missing model returns None (Finding 1)
    missing = await backend.describe_model("custom-model")
    assert missing is None

    # Auth error check_access: 401 -> INVALID_KEY, 403 -> NO_ACCESS (Finding 7)
    backend_auth = OpenAIChatBackend(
        entry,
        api_key="k",
        transport=httpx.MockTransport(lambda r: httpx.Response(401)),
    )
    assert await backend_auth.check_access() == AccessStatus.INVALID_KEY

    backend_403 = OpenAIChatBackend(
        entry,
        api_key="k",
        transport=httpx.MockTransport(lambda r: httpx.Response(403)),
    )
    assert await backend_403.check_access() == AccessStatus.NO_ACCESS

    # Empty models list for OpenAI -> NO_ACCESS
    backend_empty = OpenAIChatBackend(
        entry,
        api_key="k",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
    )
    assert await backend_empty.check_access() == AccessStatus.NO_ACCESS

    # Connect error on local provider -> NOT_RUNNING
    local_entry = ProviderEntry(
        name="local-proxy",
        title="Local Proxy",
        shape="openai_chat",
        base_url="http://127.0.0.1:8000/v1",
        secret=None,
        local=True,
        docs_url="",
    )

    def down_handler(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    backend_local = OpenAIChatBackend(
        local_entry, transport=httpx.MockTransport(down_handler)
    )
    assert await backend_local.check_access() == AccessStatus.NOT_RUNNING

    # Connect error on remote provider -> UNREACHABLE and describe_model -> None
    backend_remote = OpenAIChatBackend(
        entry, api_key="k", transport=httpx.MockTransport(down_handler)
    )
    assert await backend_remote.check_access() == AccessStatus.UNREACHABLE
    assert await backend_remote.describe_model("gpt-4o") is None


async def test_gemini_thought_signature_captured_and_replayed() -> None:
    """Gemini thought_signature is captured and replayed on assistant turn."""
    requests: list[httpx.Request] = []
    chunk1: dict[str, Any] = {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_gemini_1",
                            "type": "function",
                            "function": {
                                "name": "echo",
                                "arguments": '{"text": "hi"}',
                            },
                            "extra_content": {
                                "google": {
                                    "thought_signature": "TEST_SIG_ABC123",
                                }
                            },
                        }
                    ],
                },
            }
        ]
    }
    chunk2: dict[str, Any] = {
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "Done."},
                "finish_reason": "stop",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, content=_sse([chunk1]))
        return httpx.Response(200, content=_sse([chunk2]))

    model = OpenAIChatModel(
        "models/gemini-3.8-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key="gemini-key",
        transport=httpx.MockTransport(handler),
    )

    # Turn 1
    deltas = [d async for d in model.stream(_USER, tools=[_ECHO_TOOL])]
    assistant_msg = agglomerate_deltas(deltas)
    assert assistant_msg.tool_calls[0].name == "echo"
    assert assistant_msg.provider_data is not None
    assert assistant_msg.provider_data["google"]["signatures_by_id"][
        "call_gemini_1"
    ] == {"google": {"thought_signature": "TEST_SIG_ABC123"}}

    # Turn 2
    tool_msg = ChatMessage(
        role="tool",
        tool_call_id="call_gemini_1",
        content="hi",
    )
    turn2_messages = [*_USER, assistant_msg, tool_msg]
    _ = [d async for d in model.stream(turn2_messages, tools=[_ECHO_TOOL])]

    assert len(requests) == 2
    body2 = json.loads(requests[1].content)
    assistant_wire = body2["messages"][1]
    assert assistant_wire["role"] == "assistant"
    assert assistant_wire["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "TEST_SIG_ABC123"}
    }


async def test_gemini_fallback_thought_signature_when_missing() -> None:
    """When sending tool call to Gemini without signature, fallback is used."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_sse([]))

    model = OpenAIChatModel(
        "models/gemini-3.8-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key="gemini-key",
        transport=httpx.MockTransport(handler),
    )

    from agentling import ToolCall

    # Restored session message without captured provider_data
    msg = ChatMessage(
        role="assistant",
        tool_calls=[
            ToolCall(id="call_legacy_1", name="echo", arguments={"text": "hi"})
        ],
    )
    messages = [
        *_USER,
        msg,
        ChatMessage(role="tool", tool_call_id="call_legacy_1", content="ok"),
    ]
    _ = [d async for d in model.stream(messages, tools=[_ECHO_TOOL])]

    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assistant_wire = body["messages"][1]
    assert assistant_wire["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "skip_thought_signature_validator"}
    }
