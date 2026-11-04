"""Unit tests for OllamaModel and OllamaBackend."""

import json
from typing import Any

import httpx
import pytest
from agentling import (
    ChatMessage,
    ModelConnectionError,
    ToolDefinition,
    agglomerate_deltas,
)

from aldwin.core.providers.base import AccessStatus
from aldwin.core.providers.ollama import (
    OllamaBackend,
    OllamaModel,
    parse_ollama_show_response,
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


def _ndjson(lines: list[dict[str, Any]]) -> bytes:
    encoded = "\n".join(json.dumps(line) for line in lines) + "\n"
    return encoded.encode("utf-8")


def ndjson_transport(
    lines: list[dict[str, Any]],
    status: int = 200,
    requests: list[httpx.Request] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(status, content=_ndjson(lines))

    return httpx.MockTransport(handler)


async def test_ollama_stream_and_tool_calls() -> None:
    lines: list[dict[str, Any]] = [
        {
            "message": {
                "role": "assistant",
                "content": "Thinking...",
                "thinking": "Let's see",
            }
        },
        {
            "message": {
                "tool_calls": [
                    {
                        "function": {
                            "name": "echo",
                            "arguments": {"text": "hello"},
                        }
                    }
                ]
            }
        },
        {"done": True, "prompt_eval_count": 15, "eval_count": 8, "done_reason": "stop"},
    ]
    model = OllamaModel("qwen2.5:8b", transport=ndjson_transport(lines))
    deltas = [d async for d in model.stream(_USER, tools=[_ECHO_TOOL])]
    msg = agglomerate_deltas(deltas)
    assert msg.reasoning == "Let's see"
    assert msg.content == "Thinking..."
    assert msg.finish_reason == "tool_calls"
    assert len(msg.tool_calls) == 1
    call = msg.tool_calls[0]
    assert call.name == "echo"
    assert call.arguments == {"text": "hello"}
    assert msg.usage is not None
    assert msg.usage.input_tokens == 15
    assert msg.usage.output_tokens == 8


async def test_ollama_num_ctx_injection() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        chunk = {
            "done": True,
            "prompt_eval_count": 5,
            "eval_count": 2,
            "done_reason": "stop",
        }
        return httpx.Response(
            200,
            content=_ndjson(
                [
                    {"message": {"role": "assistant", "content": "hi"}},
                    chunk,
                ]
            ),
        )

    model = OllamaModel(
        "qwen2.5:8b",
        context_window=65536,
        transport=httpx.MockTransport(handler),
    )
    _ = [d async for d in model.stream(_USER)]
    assert len(captured) == 1
    req_body = json.loads(captured[0].content)
    assert req_body["options"]["num_ctx"] == 32768


async def test_ollama_connection_refused_local_true() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    model = OllamaModel("qwen2.5:8b", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelConnectionError) as exc_info:
        _ = [d async for d in model.stream(_USER)]
    assert getattr(exc_info.value, "local", False) is True


def test_parse_ollama_show_response() -> None:
    data = {
        "capabilities": ["tools", "vision"],
        "model_info": {
            "general.architecture": "qwen2",
            "qwen2.context_length": 32768,
        },
    }
    supports_tools, context_window = parse_ollama_show_response(data)
    assert supports_tools is True
    assert context_window == 32768

    # Chat-only without tools capability and missing context_length
    data_chat_only: dict[str, Any] = {"capabilities": []}
    tools, ctx = parse_ollama_show_response(data_chat_only)
    assert tools is False
    assert ctx == 2048


async def test_ollama_backend_listing_and_describe() -> None:
    tags_data = {
        "models": [
            {"name": "qwen2.5:8b"},
            {"model": "llama3.2:3b"},
        ]
    }
    show_data = {
        "capabilities": ["tools"],
        "model_info": {"llama.context_length": 131072},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags_data)
        if request.url.path == "/api/show":
            return httpx.Response(200, json=show_data)
        return httpx.Response(404)

    entry = ProviderEntry(
        name="ollama",
        title="Ollama",
        shape="ollama",
        base_url="http://localhost:11434",
        secret=None,
        local=True,
        docs_url="",
    )
    backend = OllamaBackend(entry, transport=httpx.MockTransport(handler))
    models = await backend.list_models()
    assert len(models) == 2
    assert models[0].id == "qwen2.5:8b"
    assert models[1].id == "llama3.2:3b"

    described = await backend.describe_model("llama3.2:3b")
    assert described is not None
    assert described.id == "llama3.2:3b"
    assert described.supports_tools is True
    assert described.context_window == 131072
    assert described.local is True

    access = await backend.check_access()
    assert access == AccessStatus.OK

    # Connect error on check_access
    def down_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    backend_down = OllamaBackend(entry, transport=httpx.MockTransport(down_handler))
    assert await backend_down.check_access() == AccessStatus.NOT_RUNNING

    # Describe model 404 returns None
    backend_404 = OllamaBackend(
        entry, transport=httpx.MockTransport(lambda r: httpx.Response(404))
    )
    assert await backend_404.describe_model("missing") is None


async def test_ollama_stream_premature_end() -> None:
    # Stream ends without done: true chunk
    lines: list[dict[str, Any]] = [{"message": {"role": "assistant", "content": "Hi"}}]
    model = OllamaModel("qwen2.5:8b", transport=ndjson_transport(lines))
    with pytest.raises(Exception, match="ended before Ollama sent its final chunk"):
        _ = [d async for d in model.stream(_USER)]


async def test_ollama_errors_and_malformed() -> None:
    from agentling import (
        ModelAuthError,
        ModelContextLengthError,
        ModelRateLimitError,
        ModelServerError,
    )

    for status, exc in [
        (401, ModelAuthError),
        (429, ModelRateLimitError),
        (500, ModelServerError),
    ]:
        transport = httpx.MockTransport(
            lambda r, s=status: httpx.Response(s, json={"error": "err"})
        )
        model = OllamaModel("qwen2.5:8b", transport=transport)
        with pytest.raises(exc):
            _ = [d async for d in model.stream(_USER)]

    # 400 with context_length_exceeded
    ctx_transport = httpx.MockTransport(
        lambda r: httpx.Response(400, text="context_length_exceeded in model")
    )
    model_ctx = OllamaModel("qwen2.5:8b", transport=ctx_transport)
    with pytest.raises(ModelContextLengthError):
        _ = [d async for d in model_ctx.stream(_USER)]

    # Stream error inside line
    err_line_transport = ndjson_transport([{"error": "runtime fail"}])
    model_err = OllamaModel("qwen2.5:8b", transport=err_line_transport)
    with pytest.raises(ModelServerError, match="runtime fail"):
        _ = [d async for d in model_err.stream(_USER)]
