"""A model adapter and backend for Anthropic's Messages API.

Streams text, thinking, and tool calls over httpx and maps HTTP failures to
Agentling's ModelError classes. It never retries; the agent loop does.

Thinking blocks are kept in provider_data["anthropic"]["blocks"] and replayed
in order with the assistant turn they came from, before any text or tool_use blocks.
"""

from __future__ import annotations

import json
import logging
import os
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

logger = logging.getLogger("aldwin.core.providers.anthropic")

API_VERSION: Final[str] = "2023-06-01"

_FINISH_REASONS: Final[dict[str, FinishReason]] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "content_filter",
}

_NAMESPACE: Final[str] = "anthropic"


class AnthropicMessagesModel:
    """Streams messages from the Anthropic Messages API."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com",
        max_tokens: int = 8192,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.api_key = (
            api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        )
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.transport = transport
        self.extra_body = dict(extra_body or {})

    def _build_headers(self) -> dict[str, str]:
        headers = {"anthropic-version": API_VERSION}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _is_local_connection(self) -> bool:
        try:
            parsed = urlsplit(self.base_url)
            host = parsed.hostname or ""
            return is_loopback_host(host)
        except Exception:
            return False

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition] = (),
    ) -> AsyncIterator[Delta]:
        """Stream one assistant turn as Deltas."""
        headers = self._build_headers()
        body = {
            **request_body(self.model, self.max_tokens, messages, tools),
            **self.extra_body,
        }
        decoder = _TurnDecoder()
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout
            ) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/v1/messages", json=body, headers=headers
                ) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise error_for_response(response)
                    async for line in response.aiter_lines():
                        for delta in decoder.decode(line):
                            yield delta
            yield decoder.finish()
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
    max_tokens: int,
    messages: Sequence[ChatMessage],
    tools: Sequence[ToolDefinition],
) -> dict[str, Any]:
    """Build the Messages API request for one streamed turn."""
    system_parts = [m.content for m in messages if m.role == "system" and m.content]
    system = "\n\n".join(system_parts) if system_parts else None

    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": _wire_messages(messages),
        "stream": True,
    }
    if system:
        body["system"] = system
    if tools:
        body["tools"] = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]
    return body


def _wire_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Convert the conversation, merging neighbours that share a role.

    Tool results travel as user content, and the API requires user and
    assistant turns to alternate.
    """
    wire: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        role = "assistant" if message.role == "assistant" else "user"
        blocks = _blocks(message)
        if not blocks:
            continue
        if wire and wire[-1]["role"] == role:
            wire[-1]["content"].extend(blocks)
        else:
            wire.append({"role": role, "content": blocks})
    return wire


def _blocks(message: ChatMessage) -> list[dict[str, Any]]:
    if message.role == "tool":
        return [
            {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
        ]
    if message.role == "user":
        return [{"type": "text", "text": message.content}] if message.content else []

    # Assistant turn: thinking blocks MUST precede text and tool_use
    blocks: list[dict[str, Any]] = list(_own_blocks(message))
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    blocks += [
        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        for call in message.tool_calls
    ]
    return blocks


def _own_blocks(message: ChatMessage) -> list[dict[str, Any]]:
    """The thinking blocks this adapter stored on an earlier turn, if any."""
    data = (message.provider_data or {}).get(_NAMESPACE)
    if not isinstance(data, dict):
        return []
    blocks = data.get("blocks")
    return [dict(block) for block in blocks] if isinstance(blocks, list) else []


class _TurnDecoder:
    """Turns one turn's server-sent events into Deltas."""

    def __init__(self) -> None:
        self.saw_event = False
        self._tool_index: dict[int, int] = {}
        self._thinking: dict[int, dict[str, Any]] = {}
        self._usage: dict[str, int] = {}
        self._stop_reason: str | None = None

    def decode(self, line: str) -> list[Delta]:
        if not line.startswith("data:"):
            return []
        self.saw_event = True
        data = line[len("data:") :].strip()
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ModelServerError(
                f"Invalid stream event: {data[:200]!r}", retryable=False
            ) from exc
        if not isinstance(event, dict):
            raise ModelServerError(
                f"Unexpected stream event: {data[:200]!r}", retryable=False
            )

        kind = event.get("type")
        try:
            if kind == "message_start":
                self._add_usage((event.get("message") or {}).get("usage"))
            elif kind == "content_block_start":
                return self._block_start(int(event["index"]), event["content_block"])
            elif kind == "content_block_delta":
                return self._block_delta(int(event["index"]), event["delta"])
            elif kind == "message_delta":
                self._stop_reason = (event.get("delta") or {}).get("stop_reason")
                self._add_usage(event.get("usage"))
            elif kind == "error":
                raise _stream_error(event.get("error"))
        except (KeyError, TypeError) as exc:
            raise ModelServerError(
                f"Malformed stream event: {data[:200]!r}", retryable=False
            ) from exc
        return []

    def finish(self) -> Delta:
        if not self.saw_event:
            raise ModelServerError(
                "The endpoint answered without an event stream. Check that "
                "base_url points at the Anthropic API.",
                retryable=False,
            )
        blocks = [self._thinking[index] for index in sorted(self._thinking)]
        return Delta(
            usage=self._total_usage(),
            finish_reason=_FINISH_REASONS.get(self._stop_reason or "", "unknown"),
            provider_data={_NAMESPACE: {"blocks": blocks}} if blocks else None,
        )

    def _block_start(self, index: int, block: dict[str, Any]) -> list[Delta]:
        kind = block.get("type")
        if kind == "text" and block.get("text"):
            return [Delta(content=block["text"])]
        if kind == "tool_use":
            self._tool_index[index] = len(self._tool_index)
            call = ToolCallDelta(
                index=self._tool_index[index],
                id=block.get("id"),
                name=block.get("name"),
            )
            return [Delta(tool_calls=[call])]
        if kind == "thinking":
            initial_text = block.get("thinking", "")
            self._thinking[index] = {
                "type": "thinking",
                "thinking": initial_text,
                "signature": block.get("signature", ""),
            }
            if initial_text:
                return [Delta(reasoning=initial_text)]
        elif kind == "redacted_thinking":
            self._thinking[index] = {"type": "redacted_thinking", "data": block["data"]}
        return []

    def _block_delta(self, index: int, delta: dict[str, Any]) -> list[Delta]:
        kind = delta.get("type")
        if kind == "text_delta" and delta.get("text"):
            return [Delta(content=delta["text"])]
        if kind == "input_json_delta" and delta.get("partial_json"):
            call = ToolCallDelta(
                index=self._tool_index[index], arguments=delta["partial_json"]
            )
            return [Delta(tool_calls=[call])]
        if kind == "thinking_delta" and index in self._thinking:
            text = delta.get("thinking", "")
            self._thinking[index]["thinking"] += text
            return [Delta(reasoning=text)] if text else []
        if kind == "signature_delta" and index in self._thinking:
            self._thinking[index]["signature"] = delta.get("signature", "")
        return []

    def _add_usage(self, raw: object) -> None:
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, int):
                    self._usage[key] = value

    def _total_usage(self) -> Usage | None:
        if not self._usage:
            return None
        cache_read = self._usage.get("cache_read_input_tokens", 0)
        return Usage(
            input_tokens=self._usage.get("input_tokens", 0)
            + cache_read
            + self._usage.get("cache_creation_input_tokens", 0),
            output_tokens=self._usage.get("output_tokens", 0),
            cached_input_tokens=cache_read,
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
    if response.has_redirect_location:
        return ModelBadRequestError(
            f"HTTP {response.status_code}: the endpoint redirects to "
            f"{response.headers['location']}. Update base_url to match.",
            status=response.status_code,
        )
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
        "prompt is too long" in response.text
        or "context_length_exceeded" in response.text
    ):
        return ModelContextLengthError(message, status=status)
    return ModelBadRequestError(message, status=status)


def _stream_error(error: object) -> ModelError:
    if not isinstance(error, dict):
        return ModelServerError(f"The model endpoint reported: {str(error)[:200]}")
    message = str(error.get("message") or "an error with no message")[:200]
    text = f"The model endpoint reported: {message}"
    err_type = error.get("type")
    if err_type == "rate_limit_error":
        return ModelRateLimitError(text)
    if err_type == "invalid_request_error":
        return ModelBadRequestError(text)
    return ModelServerError(text)


def _error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return str(error["message"])[:200]
    return response.text[:200]


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers["retry-after"]))
    except (KeyError, ValueError):
        return None


class AnthropicBackend:
    """Backend managing Anthropic Claude endpoints."""

    def __init__(
        self,
        entry: ProviderEntry,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.entry = entry
        self.api_key = api_key
        self.transport = transport
        self.timeout = timeout
        self.base_url = entry.base_url.rstrip("/")

    def build_model(self, model_id: str) -> AnthropicMessagesModel:
        return AnthropicMessagesModel(
            model=model_id,
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
        )

    def _build_headers(self) -> dict[str, str]:
        headers = {"anthropic-version": API_VERSION}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def list_models(self) -> list[ModelInfo]:
        """Fetch models from GET /v1/models."""
        headers = self._build_headers()
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout
            ) as client:
                resp = await client.get(f"{self.base_url}/v1/models", headers=headers)
                if resp.status_code != 200:
                    raise error_for_response(resp)
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(f"Request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise _connection_error(self.base_url, exc, is_local=False) from exc

        raw_models = data.get("data", []) if isinstance(data, dict) else []
        results: list[ModelInfo] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not model_id or not isinstance(model_id, str):
                continue
            title = item.get("display_name") or model_id
            results.append(
                ModelInfo(
                    id=model_id,
                    title=str(title),
                    context_window=200000,
                    supports_tools=True,
                    local=False,
                )
            )
        return results

    async def check_access(self) -> AccessStatus:
        """Verify reachability and API key."""
        try:
            await self.list_models()
            return AccessStatus.OK
        except ModelAuthError as exc:
            if exc.status == 403 or "403" in str(exc):
                return AccessStatus.NO_ACCESS
            return AccessStatus.INVALID_KEY
        except ModelConnectionError:
            return AccessStatus.UNREACHABLE
        except (ModelTimeoutError, ModelServerError, ModelBadRequestError):
            return AccessStatus.UNREACHABLE
        except Exception:
            return AccessStatus.UNREACHABLE

    async def describe_model(self, model_id: str) -> ModelInfo | None:
        """Return model metadata from model list, or None if unavailable."""
        try:
            models = await self.list_models()
        except Exception:
            return None
        for m in models:
            if m.id == model_id:
                return m
        return None
