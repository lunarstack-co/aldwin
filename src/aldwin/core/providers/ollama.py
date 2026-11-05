"""A model adapter and backend for Ollama's native API.

Covers /api/chat, /api/tags, and /api/show. Streams text, thinking, and
tool calls over httpx and maps HTTP failures to Agentling's ModelError classes.
Connection failures to local Ollama endpoints always raise ModelConnectionError
with local=True.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from typing import Any, Final
from urllib.parse import urlsplit

import httpx
from agentling import (
    ChatMessage,
    Delta,
    FinishReason,
    ModelAuthError,
    ModelBadRequestError,
    ModelConnectionError,
    ModelContextLengthError,
    ModelError,
    ModelRateLimitError,
    ModelServerError,
    ModelTimeoutError,
    ToolCallDelta,
    ToolDefinition,
    Usage,
)

from aldwin.core.providers.base import AccessStatus, ModelInfo
from aldwin.core.providers.registry import ProviderEntry, is_loopback_host

logger = logging.getLogger("aldwin.core.providers.ollama")

_FINISH_REASONS: Final[dict[str, FinishReason]] = {
    "stop": "stop",
    "length": "length",
}


def parse_ollama_show_response(data: dict[str, Any]) -> tuple[bool | None, int]:
    """Parse /api/show response to determine tool support and context length.

    Shared between OllamaBackend and the local runtime finder.

    Returns:
        (supports_tools, context_window)
    """
    capabilities = data.get("capabilities")
    if capabilities is None:
        supports_tools = None
    elif isinstance(capabilities, list):
        supports_tools = "tools" in capabilities
    else:
        supports_tools = False

    context_length = 2048
    model_info = data.get("model_info") or {}
    if isinstance(model_info, dict):
        for key, val in model_info.items():
            if key.endswith(".context_length") and isinstance(val, (int, float)):
                context_length = int(val)
                break

    return supports_tools, context_length


class OllamaModel:
    """Streams chat responses from Ollama's native API."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://localhost:11434",
        api_key: str | None = None,
        context_window: int = 32768,
        timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.context_window = context_window
        self.timeout = timeout
        self.transport = transport
        self.extra_body = dict(extra_body or {})
        self.local: bool = True

    def _is_local_connection(self) -> bool:
        try:
            parsed = urlsplit(self.base_url)
            host = parsed.hostname or ""
            return is_loopback_host(host) or host in ("127.0.0.1", "localhost", "::1")
        except Exception:
            return True

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition] = (),
    ) -> AsyncIterator[Delta]:
        """Stream one assistant turn as Deltas."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        options = {"num_ctx": min(self.context_window, 32768)}
        merged_body = {
            **request_body(self.model, messages, tools, options=options),
            **self.extra_body,
        }
        decoder = _TurnDecoder()
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout
            ) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=merged_body,
                    headers=headers,
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise error_for_response(response)
                    async for line in response.aiter_lines():
                        for delta in decoder.decode(line):
                            yield delta
            if not decoder.done:
                raise ModelServerError(
                    "The stream ended before Ollama sent its final chunk.",
                    retryable=False,
                )
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(f"Request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise _connection_error(
                self.base_url, exc, self._is_local_connection()
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelServerError(f"Bad response from {self.base_url}: {exc}") from exc


def request_body(
    model: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDefinition],
    *,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the /api/chat request for one streamed turn."""
    body: dict[str, Any] = {
        "model": model,
        "messages": _wire_messages(messages),
        "stream": True,
    }
    if options:
        body["options"] = options
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
    return body


def _wire_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    tool_names: dict[str, str] = {}
    wire: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            wire.append(
                {
                    "role": "tool",
                    "content": message.content,
                    "tool_name": tool_names.get(message.tool_call_id or "", ""),
                }
            )
            continue
        entry: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.role == "assistant":
            if message.reasoning:
                entry["thinking"] = message.reasoning
            if message.tool_calls:
                entry["tool_calls"] = [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in message.tool_calls
                ]
                tool_names.update({call.id: call.name for call in message.tool_calls})
        wire.append(entry)
    return wire


class _TurnDecoder:
    """Turns one turn's NDJSON lines into Deltas."""

    def __init__(self) -> None:
        self.done = False
        self._calls = 0

    def decode(self, line: str) -> list[Delta]:
        if not line.strip():
            return []
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ModelServerError(
                f"Invalid stream chunk: {line[:200]!r}", retryable=False
            ) from exc
        if not isinstance(chunk, dict):
            raise ModelServerError(
                f"Unexpected stream chunk: {line[:200]!r}", retryable=False
            )
        if chunk.get("error"):
            raise ModelServerError(
                f"The model endpoint reported: {str(chunk['error'])[:200]}"
            )

        message = chunk.get("message") or {}
        if not isinstance(message, dict) or not all(
            isinstance(call, dict) for call in message.get("tool_calls") or []
        ):
            raise ModelServerError(
                f"Malformed stream chunk: {line[:200]!r}", retryable=False
            )
        deltas: list[Delta] = []
        if message.get("content") or message.get("thinking"):
            deltas.append(
                Delta(
                    content=message.get("content") or None,
                    reasoning=message.get("thinking") or None,
                )
            )
        for call in message.get("tool_calls") or []:
            deltas.append(Delta(tool_calls=[self._tool_call(call)]))
        if chunk.get("done"):
            self.done = True
            deltas.append(self._final(chunk))
        return deltas

    def _tool_call(self, call: dict[str, Any]) -> ToolCallDelta:
        function = call.get("function") or {}
        index = function.get("index")
        if not isinstance(index, int):
            index = self._calls
        self._calls += 1
        arguments = function.get("arguments") or {}
        args_str = (
            json.dumps(arguments) if not isinstance(arguments, str) else arguments
        )
        return ToolCallDelta(
            index=index,
            id=call.get("id") or None,
            name=function.get("name"),
            arguments=args_str,
        )

    def _final(self, chunk: dict[str, Any]) -> Delta:
        reason: FinishReason = (
            "tool_calls"
            if self._calls
            else _FINISH_REASONS.get(chunk.get("done_reason") or "", "unknown")
        )
        return Delta(
            usage=Usage(
                input_tokens=chunk.get("prompt_eval_count") or 0,
                output_tokens=chunk.get("eval_count") or 0,
            ),
            finish_reason=reason,
        )


def _connection_error(
    base_url: str, exc: Exception, is_local: bool
) -> ModelConnectionError:
    err = ModelConnectionError(
        f"Could not reach {base_url}: {exc}",
        retryable=not is_local,
    )
    setattr(err, "local", is_local)
    return err


def error_for_response(response: httpx.Response) -> ModelError:
    status = response.status_code
    message = f"HTTP {status} from the model endpoint: {_error_text(response)}"
    if status in (401, 403) or "API_KEY_INVALID" in response.text:
        return ModelAuthError(message, status=status)
    if status == 429:
        return ModelRateLimitError(
            message, status=status, retry_after=_retry_after(response)
        )
    if status >= 500:
        return ModelServerError(message, status=status)
    if (
        "context_length_exceeded" in response.text
        or "prompt is too long" in response.text
    ):
        return ModelContextLengthError(message, status=status)
    return ModelBadRequestError(message, status=status)


def _error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return str(payload["error"])[:200]
    return response.text[:200]


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers["retry-after"]))
    except (KeyError, ValueError):
        return None


class OllamaBackend:
    """Backend managing Ollama native endpoints."""

    def __init__(
        self,
        entry: ProviderEntry,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.entry = entry
        self.api_key = api_key
        self.transport = transport
        self.timeout = timeout
        self.base_url = entry.base_url.rstrip("/")

    def build_model(self, model_id: str) -> OllamaModel:
        return OllamaModel(
            model=model_id,
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
            transport=self.transport,
        )

    def _is_local_connection(self) -> bool:
        try:
            parsed = urlsplit(self.base_url)
            host = parsed.hostname or ""
            return is_loopback_host(host) or host in ("127.0.0.1", "localhost", "::1")
        except Exception:
            return True

    async def list_models(self) -> list[ModelInfo]:
        """Fetch model tags from GET /api/tags."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout
            ) as client:
                resp = await client.get(f"{self.base_url}/api/tags", headers=headers)
                if resp.status_code != 200:
                    raise error_for_response(resp)
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(f"Request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise _connection_error(
                self.base_url, exc, is_local=self._is_local_connection()
            ) from exc

        raw_models = data.get("models", []) if isinstance(data, dict) else []
        results: list[ModelInfo] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("model")
            if not name or not isinstance(name, str):
                continue
            results.append(
                ModelInfo(
                    id=name,
                    title=name,
                    context_window=2048,
                    supports_tools=True,
                    local=True,
                )
            )
        return results

    async def check_access(self) -> AccessStatus:
        """Verify reachability of local Ollama server."""
        try:
            await self.list_models()
            return AccessStatus.OK
        except ModelAuthError:
            return AccessStatus.INVALID_KEY
        except ModelConnectionError as exc:
            if getattr(exc, "local", False):
                return AccessStatus.NOT_RUNNING
            return AccessStatus.UNREACHABLE
        except Exception:
            return AccessStatus.UNREACHABLE

    async def describe_model(self, model_id: str) -> ModelInfo | None:
        """Query /api/show to fetch accurate tool capabilities and context length."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout
            ) as client:
                resp = await client.post(
                    f"{self.base_url}/api/show",
                    json={"model": model_id},
                    headers=headers,
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
        except Exception:
            return None

        supports_tools, context_window = parse_ollama_show_response(data)
        return ModelInfo(
            id=model_id,
            title=model_id,
            context_window=context_window,
            supports_tools=supports_tools,
            local=True,
        )
