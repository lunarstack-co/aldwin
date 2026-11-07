"""A model adapter and backend for OpenAI-compatible chat-completions endpoints.

Works with OpenAI and with servers that copy its API (LM Studio, vLLM, Ollama's
/v1, OpenRouter, Google Gemini OpenAI-compat, DeepSeek, and others): point
`base_url` at them. Streams text, reasoning, and tool calls over httpx and maps
HTTP failures to Agentling's ModelError classes. It never retries; the agent
loop does.
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

logger = logging.getLogger("aldwin.core.providers.openai_chat")

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


def is_chat_model(model_id: str) -> bool:
    """Return `True` if `model_id` is an OpenAI chat-completions model."""
    lowered = model_id.strip().lower()
    if not lowered or not lowered.startswith(_CHAT_MODEL_PREFIXES):
        return False
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


_FINISH_REASONS: Final[dict[str, FinishReason]] = {
    "stop": "stop",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "length": "length",
    "content_filter": "content_filter",
    "error": "error",
}

# Base URLs confirmed to reject the stream_options parameter.
_BASE_URLS_WITHOUT_STREAM_OPTIONS: set[str] = set()


class OpenAIChatModel:
    """Streams chat completions from an OpenAI-compatible endpoint."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        extra_body: dict[str, Any] | None = None,
        local: bool = False,
    ) -> None:
        """Create the adapter.

        Args:
            model: The model name the endpoint expects.
            api_key: Sent as a bearer token. Defaults to OPENAI_API_KEY; local
                servers usually need none.
            base_url: The endpoint's base URL, up to and including /v1.
            timeout: httpx timeout in seconds for connecting and each read.
            transport: An httpx transport to use instead of the network
                (tests pass an httpx.MockTransport).
            extra_body: Fields merged into every request body last, so they
                win: sampling settings such as temperature, or a
                server's own options.
            local: Whether the model runs locally.
        """
        self.model = model
        self.api_key = (
            api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.extra_body = dict(extra_body or {})
        self.local = local

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in self.base_url.lower():
            headers["HTTP-Referer"] = "https://askaldwin.com"
            headers["X-Title"] = "Aldwin"
        return headers

    def _is_local_connection(self) -> bool:
        if self.local:
            return True
        try:
            parsed = urlsplit(self.base_url)
            host = parsed.hostname or ""
            return is_loopback_host(host)
        except Exception:
            return False

    def _is_google_endpoint(self) -> bool:
        lowered_url = self.base_url.lower()
        lowered_model = self.model.lower()
        return "googleapis.com" in lowered_url or "gemini" in lowered_model

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDefinition] = (),
    ) -> AsyncIterator[Delta]:
        """Stream one assistant turn as Deltas."""
        headers = self._build_headers()
        url_key = self.base_url.rstrip("/").lower()
        include_stream_options = url_key not in _BASE_URLS_WITHOUT_STREAM_OPTIONS
        is_google = self._is_google_endpoint()
        decoder = _TurnDecoder()

        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout
            ) as client:
                body = {
                    **request_body(
                        self.model,
                        messages,
                        tools,
                        include_stream_options=include_stream_options,
                        is_google=is_google,
                    ),
                    **self.extra_body,
                }
                retry_without_stream_options = False
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers=headers,
                ) as response:
                    if response.status_code == 400 and include_stream_options:
                        error_bytes = await response.aread()
                        error_text = error_bytes.decode(errors="replace")
                        if "stream_options" in error_text:
                            _BASE_URLS_WITHOUT_STREAM_OPTIONS.add(url_key)
                            retry_without_stream_options = True
                        else:
                            raise error_for_response(response)
                    elif response.status_code != 200:
                        await response.aread()
                        raise error_for_response(response)
                    else:
                        async for line in response.aiter_lines():
                            delta = decoder.decode(line)
                            if delta is not None:
                                yield delta

                        if not decoder.saw_event:
                            content_type = response.headers.get("content-type", "none")
                            raise ModelServerError(
                                "The endpoint answered without an event stream "
                                f"(Content-Type: {content_type}). Check that base_url "
                                "points at an OpenAI-compatible API, usually ending in "
                                "/v1.",
                                retryable=False,
                            )

                if retry_without_stream_options:
                    retry_body = {
                        **request_body(
                            self.model,
                            messages,
                            tools,
                            include_stream_options=False,
                            is_google=is_google,
                        ),
                        **self.extra_body,
                    }
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/chat/completions",
                        json=retry_body,
                        headers=headers,
                    ) as retry_response:
                        if retry_response.status_code != 200:
                            await retry_response.aread()
                            raise error_for_response(retry_response)
                        async for line in retry_response.aiter_lines():
                            delta = decoder.decode(line)
                            if delta is not None:
                                yield delta
                        if not decoder.saw_event:
                            content_type = retry_response.headers.get(
                                "content-type", "none"
                            )
                            raise ModelServerError(
                                "The endpoint answered without an event stream "
                                f"(Content-Type: {content_type}). Check that "
                                "base_url points at an OpenAI-compatible API, "
                                "usually ending in /v1.",
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
    include_stream_options: bool = True,
    is_google: bool = False,
) -> dict[str, Any]:
    """Build the chat-completions request for one streamed turn."""
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            _wire_message(message, is_google=is_google) for message in messages
        ],
        "stream": True,
    }
    if include_stream_options:
        body["stream_options"] = {"include_usage": True}
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


def _wire_message(
    message: ChatMessage,
    *,
    is_google: bool = False,
) -> dict[str, Any]:
    """Convert one ChatMessage to the chat-completions message shape."""
    if message.role == "tool":
        return {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }
    if message.role == "assistant":
        wire: dict[str, Any] = {"role": "assistant", "content": message.content or None}
        if message.tool_calls:
            google_data = (message.provider_data or {}).get("google") or {}
            sig_by_id = google_data.get("signatures_by_id") or {}
            sig_by_index = google_data.get("signatures_by_index") or {}

            calls: list[dict[str, Any]] = []
            for idx, call in enumerate(message.tool_calls):
                call_dict: dict[str, Any] = {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                extra = sig_by_id.get(call.id) or sig_by_index.get(idx)
                if extra:
                    call_dict["extra_content"] = extra
                elif is_google and idx == 0:
                    call_dict["extra_content"] = {
                        "google": {
                            "thought_signature": "skip_thought_signature_validator"
                        }
                    }
                calls.append(call_dict)
            wire["tool_calls"] = calls
        return wire
    return {"role": message.role, "content": message.content}


class _TurnDecoder:
    """Turns one turn's server-sent-event lines into Deltas."""

    def __init__(self) -> None:
        self.saw_event = False
        self._latest_call = -1
        self._call_by_id: dict[str, int] = {}
        self._extra_content_by_id: dict[str, Any] = {}
        self._extra_content_by_index: dict[int, Any] = {}

    def decode(self, line: str) -> Delta | None:
        """Turn one line into a Delta, or None when it carries nothing."""
        if not line.startswith("data:"):
            return None
        self.saw_event = True
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            return None
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ModelServerError(
                f"Invalid stream chunk: {data[:200]!r}", retryable=False
            ) from exc
        if not isinstance(chunk, dict):
            raise ModelServerError(
                f"Unexpected stream chunk: {data[:200]!r}", retryable=False
            )
        if chunk.get("error"):
            raise _stream_error(chunk["error"])
        return self._delta(chunk)

    def _delta(self, chunk: dict[str, Any]) -> Delta | None:
        choices = chunk.get("choices") or []
        choice: dict[str, Any] = choices[0] if choices else {}
        wire_delta: dict[str, Any] = choice.get("delta") or {}
        raw_reason = choice.get("finish_reason")
        raw_usage = chunk.get("usage")

        for call in wire_delta.get("tool_calls") or []:
            call_id = call.get("id")
            index = call.get("index")
            extra = call.get("extra_content")
            if extra:
                if call_id:
                    self._extra_content_by_id[call_id] = extra
                if isinstance(index, int):
                    self._extra_content_by_index[index] = extra

        provider_data: dict[str, Any] | None = None
        if self._extra_content_by_id or self._extra_content_by_index:
            provider_data = {
                "google": {
                    "signatures_by_id": dict(self._extra_content_by_id),
                    "signatures_by_index": dict(self._extra_content_by_index),
                }
            }

        delta = Delta(
            content=wire_delta.get("content") or None,
            reasoning=wire_delta.get("reasoning_content") or None,
            tool_calls=[
                self._tool_call(call) for call in wire_delta.get("tool_calls") or []
            ],
            usage=_usage(raw_usage) if raw_usage else None,
            finish_reason=_FINISH_REASONS.get(raw_reason, "unknown")
            if raw_reason
            else None,
            provider_data=provider_data,
        )
        if not (
            delta.content
            or delta.reasoning
            or delta.tool_calls
            or delta.usage
            or delta.finish_reason
            or delta.provider_data
        ):
            return None
        return delta

    def _tool_call(self, call: dict[str, Any]) -> ToolCallDelta:
        function = call.get("function") or {}
        call_id = call.get("id") or None
        name = function.get("name") or None
        return ToolCallDelta(
            index=self._index_for(call.get("index"), call_id, name),
            id=call_id,
            name=name,
            arguments=function.get("arguments"),
        )

    def _index_for(self, index: object, call_id: str | None, name: str | None) -> int:
        if isinstance(index, int):
            if call_id is not None:
                self._call_by_id.setdefault(call_id, index)
            self._latest_call = max(self._latest_call, index)
            return index
        if call_id is not None:
            if call_id not in self._call_by_id:
                self._latest_call += 1
                self._call_by_id[call_id] = self._latest_call
            return self._call_by_id[call_id]
        if name is not None:
            self._latest_call += 1
        return max(self._latest_call, 0)


def _usage(raw: dict[str, Any]) -> Usage:
    prompt_details = raw.get("prompt_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or {}
    return Usage(
        input_tokens=raw.get("prompt_tokens") or 0,
        output_tokens=raw.get("completion_tokens") or 0,
        cached_input_tokens=prompt_details.get("cached_tokens") or 0,
        reasoning_tokens=completion_details.get("reasoning_tokens") or 0,
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
    """Map a failed HTTP response to the ModelError class the loop expects."""
    if response.has_redirect_location:
        return ModelBadRequestError(
            f"HTTP {response.status_code}: the endpoint redirects to "
            f"{response.headers['location']}. Update base_url to match.",
            status=response.status_code,
        )
    return _classify(
        response.status_code,
        _error_text(response),
        response.text,
        _retry_after(response),
    )


def _stream_error(error: object) -> ModelError:
    if not isinstance(error, dict):
        return ModelServerError(f"The model endpoint reported: {str(error)[:200]}")
    message = str(error.get("message") or "an error with no message")[:200]
    code = error.get("code")
    if isinstance(code, int):
        return _classify(code, message, json.dumps(error), retry_after=None)
    return ModelServerError(f"The model endpoint reported: {message}")


def _classify(
    status: int, message: str, body: str, retry_after: float | None
) -> ModelError:
    message = f"HTTP {status} from the model endpoint: {message}"
    if status in (401, 403) or "API_KEY_INVALID" in body:
        return ModelAuthError(message, status=status)
    if status == 429:
        return ModelRateLimitError(message, status=status, retry_after=retry_after)
    if status >= 500:
        return ModelServerError(message, status=status)
    if "context_length_exceeded" in body:
        return ModelContextLengthError(message, status=status)
    return ModelBadRequestError(message, status=status)


def _error_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, list) and payload:
        payload = payload[0]
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


class OpenAIChatBackend:
    """Backend managing OpenAI-compatible endpoints."""

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

    def build_model(self, model_id: str) -> OpenAIChatModel:
        return OpenAIChatModel(
            model=model_id,
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
            local=self.entry.local,
        )

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if "openrouter.ai" in self.base_url.lower():
            headers["HTTP-Referer"] = "https://askaldwin.com"
            headers["X-Title"] = "Aldwin"
        return headers

    async def list_models(self) -> list[ModelInfo]:
        """Fetch models from GET /models."""
        headers = self._build_headers()
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout
            ) as client:
                resp = await client.get(f"{self.base_url}/models", headers=headers)
                if resp.status_code != 200:
                    raise error_for_response(resp)
                data = resp.json()
        except httpx.TimeoutException as exc:
            raise ModelTimeoutError(f"Request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            is_local = self.entry.local or is_loopback_host(
                urlsplit(self.base_url).hostname or ""
            )
            raise _connection_error(self.base_url, exc, is_local) from exc

        raw_models = data.get("data", []) if isinstance(data, dict) else []
        results: list[ModelInfo] = []
        is_openai_provider = self.entry.name.lower() == "openai"
        is_lmstudio = self.entry.name.lower() == "lmstudio"

        if is_openai_provider:
            # Sort newest first by created timestamp (tie-broken alphabetically by ID)
            by_id: dict[str, tuple[int, dict[str, Any]]] = {}
            for item in raw_models:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("id", "")).strip()
                if not mid or not is_chat_model(mid):
                    continue
                created_raw = item.get("created", 0)
                created = (
                    int(created_raw) if isinstance(created_raw, (int, float)) else 0
                )
                if mid not in by_id or created > by_id[mid][0]:
                    by_id[mid] = (created, item)
            sorted_items = [
                pair[1][1]
                for pair in sorted(by_id.items(), key=lambda p: (-p[1][0], p[0]))
            ]
            raw_models = sorted_items

        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not model_id or not isinstance(model_id, str):
                continue

            if is_openai_provider and not is_chat_model(model_id):
                continue

            supports_tools = True
            if is_lmstudio:
                capabilities = item.get("capabilities") or {}
                if (
                    isinstance(capabilities, dict)
                    and "trained_for_tool_use" in capabilities
                ):
                    supports_tools = bool(capabilities["trained_for_tool_use"])

            title = item.get("name") or model_id
            results.append(
                ModelInfo(
                    id=model_id,
                    title=str(title),
                    context_window=128000,
                    supports_tools=supports_tools,
                    local=self.entry.local,
                )
            )
        return results

    async def _check_key_path(self, path: str) -> AccessStatus | None:
        """Return a failing status from the provider's key endpoint, else None."""
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=self.timeout
            ) as client:
                resp = await client.get(
                    f"{self.base_url}{path}", headers=self._build_headers()
                )
        except httpx.HTTPError:
            return AccessStatus.UNREACHABLE
        if resp.status_code in (401, 403):
            return AccessStatus.INVALID_KEY
        if resp.status_code != 200:
            return AccessStatus.UNREACHABLE
        return None

    async def check_access(self) -> AccessStatus:
        """Verify reachability and credentials."""
        if self.entry.key_check_path:
            failed = await self._check_key_path(self.entry.key_check_path)
            if failed is not None:
                return failed
        try:
            models = await self.list_models()
            if not models and self.entry.name.lower() == "openai":
                return AccessStatus.NO_ACCESS
            return AccessStatus.OK
        except ModelAuthError as exc:
            if exc.status == 403 or "403" in str(exc):
                return AccessStatus.NO_ACCESS
            return AccessStatus.INVALID_KEY
        except ModelConnectionError as exc:
            if getattr(exc, "local", False) or self.entry.local:
                return AccessStatus.NOT_RUNNING
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
