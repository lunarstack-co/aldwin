"""Unit tests for AnthropicMessagesModel and AnthropicBackend."""

import json
from typing import Any

import httpx
import pytest
from agentling import (
    ChatMessage,
    ModelAuthError,
    ModelRateLimitError,
    ModelServerError,
    ToolCall,
    ToolDefinition,
    agglomerate_deltas,
)

from aldwin.core.providers.anthropic import (
    AnthropicBackend,
    AnthropicMessagesModel,
    request_body,
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


def _anthropic_sse(events: list[dict[str, Any]]) -> bytes:
    lines = [f"data: {json.dumps(event)}\n\n" for event in events]
    return "".join(lines).encode("utf-8")


def anthropic_sse_transport(
    events: list[dict[str, Any]],
    status: int = 200,
    headers: dict[str, str] | None = None,
    requests: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        content = _anthropic_sse(events)
        return httpx.Response(status, headers=headers, content=content)

    return httpx.MockTransport(handler)


def test_anthropic_system_prompt_lifting() -> None:
    messages = [
        ChatMessage(role="system", content="System instruction 1"),
        ChatMessage(role="system", content="System instruction 2"),
        ChatMessage(role="user", content="Hello"),
    ]
    body = request_body("claude-3-7-sonnet", 8192, messages, tools=[])
    assert body["system"] == "System instruction 1\n\nSystem instruction 2"
    assert len(body["messages"]) == 1
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == [{"type": "text", "text": "Hello"}]


def test_anthropic_message_merging() -> None:
    messages = [
        ChatMessage(role="user", content="What is the weather?"),
        ChatMessage(
            role="assistant",
            tool_calls=[
                ToolCall(
                    id="call_weather", name="weather", arguments={"city": "London"}
                )
            ],
        ),
        ChatMessage(role="tool", content="Rainy, 12C", tool_call_id="call_weather"),
        ChatMessage(role="user", content="And in Paris?"),
    ]
    body = request_body("claude-3-7-sonnet", 8192, messages, tools=[])
    assert len(body["messages"]) == 3
    # Turn 0: user text
    assert body["messages"][0]["role"] == "user"
    # Turn 1: assistant tool_use
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][1]["content"][0]["id"] == "call_weather"
    # Turn 2: merged user message containing tool_result AND follow-up user text
    assert body["messages"][2]["role"] == "user"
    assert len(body["messages"][2]["content"]) == 2
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert body["messages"][2]["content"][0]["tool_use_id"] == "call_weather"
    assert body["messages"][2]["content"][1]["type"] == "text"
    assert body["messages"][2]["content"][1]["text"] == "And in Paris?"


async def test_anthropic_thinking_extraction() -> None:
    events: list[dict[str, Any]] = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "thinking",
                "thinking": "Let me ",
                "signature": "sig123",
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "think."},
        },
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "text", "text": "Answer"},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": " here."},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 8},
        },
    ]
    transport = anthropic_sse_transport(events)
    model = AnthropicMessagesModel(
        "claude-3-7-sonnet", api_key="k", transport=transport
    )
    deltas = [d async for d in model.stream(_USER)]
    msg = agglomerate_deltas(deltas)
    assert msg.reasoning == "Let me think."
    assert msg.content == "Answer here."
    assert msg.finish_reason == "stop"

    # Terminal delta provider_data check
    terminal_delta = deltas[-1]
    assert terminal_delta.provider_data is not None
    assert "anthropic" in terminal_delta.provider_data
    blocks = terminal_delta.provider_data["anthropic"]["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "thinking"
    assert blocks[0]["thinking"] == "Let me think."
    assert blocks[0]["signature"] == "sig123"


def test_anthropic_thinking_replay() -> None:
    # History assistant message that produced thinking + tool call
    history_msg = ChatMessage(
        role="assistant",
        content="I will call echo.",
        tool_calls=[ToolCall(id="call_echo", name="echo", arguments={"text": "hi"})],
        provider_data={
            "anthropic": {
                "blocks": [
                    {
                        "type": "thinking",
                        "thinking": "Thinking text...",
                        "signature": "sig_abc",
                    }
                ]
            }
        },
    )
    messages = [
        ChatMessage(role="user", content="hi"),
        history_msg,
        ChatMessage(role="tool", content="hi", tool_call_id="call_echo"),
    ]
    body = request_body("claude-3-7-sonnet", 8192, messages, tools=[_ECHO_TOOL])
    assistant_wire = body["messages"][1]
    assert assistant_wire["role"] == "assistant"
    # Thinking block MUST precede text and tool_use in the wire content blocks
    assert assistant_wire["content"][0]["type"] == "thinking"
    assert assistant_wire["content"][0]["thinking"] == "Thinking text..."
    assert assistant_wire["content"][0]["signature"] == "sig_abc"
    assert assistant_wire["content"][1]["type"] == "text"
    assert assistant_wire["content"][2]["type"] == "tool_use"


def test_anthropic_default_max_tokens() -> None:
    model = AnthropicMessagesModel("claude-3-7-sonnet", api_key="k")
    assert model.max_tokens == 8192
    body = request_body("claude-3-7-sonnet", model.max_tokens, _USER, tools=[])
    assert body["max_tokens"] == 8192


async def test_anthropic_error_mapping() -> None:
    for status, exc_type in [
        (401, ModelAuthError),
        (429, ModelRateLimitError),
        (529, ModelServerError),
    ]:
        transport = httpx.MockTransport(
            lambda r, s=status: httpx.Response(s, json={"error": {"message": "failed"}})
        )
        model = AnthropicMessagesModel(
            "claude-3-7-sonnet", api_key="k", transport=transport
        )
        with pytest.raises(exc_type):
            _ = [d async for d in model.stream(_USER)]


async def test_anthropic_list_models() -> None:
    data = {
        "data": [
            {"id": "claude-3-7-sonnet-20250219", "display_name": "Claude 3.7 Sonnet"},
            {"id": "claude-3-5-haiku-20241022", "display_name": "Claude 3.5 Haiku"},
        ]
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=data))
    entry = ProviderEntry(
        name="anthropic",
        title="Anthropic",
        shape="anthropic",
        base_url="https://api.anthropic.com",
        secret="anthropic_api_key",
        local=False,
        docs_url="",
    )
    backend = AnthropicBackend(entry, api_key="k", transport=transport)
    models = await backend.list_models()
    assert len(models) == 2
    sonnet = models[0]
    assert sonnet.id == "claude-3-7-sonnet-20250219"
    assert sonnet.title == "Claude 3.7 Sonnet"
    assert sonnet.context_window == 200000
    assert sonnet.supports_tools is True
    assert sonnet.local is False


async def test_anthropic_describe_and_check_access() -> None:
    data = {
        "data": [
            {"id": "claude-3-7-sonnet-20250219", "display_name": "Claude 3.7 Sonnet"},
        ]
    }
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=data))
    entry = ProviderEntry(
        name="anthropic",
        title="Anthropic",
        shape="anthropic",
        base_url="https://api.anthropic.com",
        secret="anthropic_api_key",
        local=False,
        docs_url="",
    )
    backend = AnthropicBackend(entry, api_key="k", transport=transport)
    assert await backend.check_access() == "ok"
    described = await backend.describe_model("claude-3-7-sonnet-20250219")
    assert described is not None
    assert described.id == "claude-3-7-sonnet-20250219"

    # Missing model ID returns None
    missing = await backend.describe_model("unknown-model")
    assert missing is None

    # Auth error check_access: 401 -> invalid_key, 403 -> no_access
    auth_fail_transport = httpx.MockTransport(lambda r: httpx.Response(401))
    backend_auth_fail = AnthropicBackend(
        entry, api_key="k", transport=auth_fail_transport
    )
    assert await backend_auth_fail.check_access() == "invalid_key"

    auth_403_transport = httpx.MockTransport(lambda r: httpx.Response(403))
    backend_403 = AnthropicBackend(entry, api_key="k", transport=auth_403_transport)
    assert await backend_403.check_access() == "no_access"

    # Connection error check_access & describe_model returns None on error
    def connect_err(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    backend_down = AnthropicBackend(
        entry, api_key="k", transport=httpx.MockTransport(connect_err)
    )
    assert await backend_down.check_access() == "unreachable"
    assert await backend_down.describe_model("claude-3-7-sonnet-20250219") is None


async def test_anthropic_stream_failure_modes() -> None:
    from agentling import ModelContextLengthError

    # Context length exceeded maps to ModelContextLengthError
    ctx_transport = httpx.MockTransport(
        lambda r: httpx.Response(
            400,
            json={
                "error": {
                    "type": "invalid_request_error",
                    "message": "prompt is too long",
                }
            },
        )
    )
    model_ctx = AnthropicMessagesModel(
        "claude-3-7-sonnet", api_key="k", transport=ctx_transport
    )
    with pytest.raises(ModelContextLengthError):
        _ = [d async for d in model_ctx.stream(_USER)]

    # Stream error with rate_limit_error maps to ModelRateLimitError
    stream_rl_transport = anthropic_sse_transport(
        [
            {
                "type": "error",
                "error": {"type": "rate_limit_error", "message": "hit limit"},
            }
        ]
    )
    model_rl = AnthropicMessagesModel(
        "claude-3-7-sonnet", api_key="k", transport=stream_rl_transport
    )
    with pytest.raises(ModelRateLimitError, match="hit limit"):
        _ = [d async for d in model_rl.stream(_USER)]

    # Empty stream without event stream raises ModelServerError
    transport_empty = httpx.MockTransport(lambda r: httpx.Response(200, content=b""))
    model = AnthropicMessagesModel(
        "claude-3-7-sonnet", api_key="k", transport=transport_empty
    )
    with pytest.raises(ModelServerError, match="without an event stream"):
        _ = [d async for d in model.stream(_USER)]

    # 301 redirect
    transport_redirect = httpx.MockTransport(
        lambda r: httpx.Response(
            301, headers={"Location": "https://api.anthropic.com/v2"}
        )
    )
    model_redir = AnthropicMessagesModel(
        "claude-3-7-sonnet", api_key="k", transport=transport_redirect
    )
    with pytest.raises(Exception) as exc_info:
        _ = [d async for d in model_redir.stream(_USER)]
    assert "redirects" in str(exc_info.value)
