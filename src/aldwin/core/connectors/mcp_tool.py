"""Connector tools as Agentling `Tool`s."""

from __future__ import annotations

import mimetypes
import secrets
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.errors import ConnectorError
from aldwin.core.connectors.naming import model_tool_name
from aldwin.core.home import atomic_write
from aldwin.core.policy import ApprovalPreview

MAX_DESCRIPTION_CHARS = 1_024
MAX_PREVIEW_VALUE_CHARS = 200
_EMPTY_OBJECT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class RemoteToolSpec:
    """What a server says about one tool, in Aldwin types (no `mcp` objects)."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    read_only_hint: bool | None = None
    destructive_hint: bool | None = None


@dataclass(frozen=True)
class ToolCallOutcome:
    """A tool result in Aldwin types, so no `mcp` object leaves this package."""

    text_parts: list[str] = field(default_factory=list)
    binary_parts: list[tuple[str, bytes]] = field(default_factory=list)
    is_error: bool = False

    @property
    def error_text(self) -> str:
        return "\n".join(self.text_parts) or "the tool reported an error"


class ToolCaller(Protocol):
    """What a tool needs from its connection; the manager can swap it on reconnect."""

    async def call_tool(
        self, name: str, arguments: dict[str, Any], *, timeout: float = ...
    ) -> ToolCallOutcome: ...


class McpTool(Tool):
    """One server tool. It returns raw text; the policy wrapper marks it untrusted."""

    validate_arguments = False
    parallel_safe = False
    timeout = 120.0
    max_output_chars = 20_000

    def __init__(
        self,
        *,
        connector: ConnectorConfig,
        spec: RemoteToolSpec,
        connection: ToolCaller,
        tmp_dir: Path,
    ) -> None:
        self.name = model_tool_name(connector.name, spec.name)
        self.description = sanitize_description(spec.description)
        self.parameters = normalize_schema(spec.input_schema)
        self.connector_name = connector.name
        self.connector_title = connector.display_title
        self.remote_name = spec.name
        self.remote_title = spec.title
        self.read_only_hint = spec.read_only_hint
        self.connection = connection
        self._tmp_dir = tmp_dir

    async def forward(self, **kwargs: Any) -> str:
        try:
            outcome = await self.connection.call_tool(
                self.remote_name, kwargs, timeout=self.timeout
            )
        except ConnectorError as exc:
            raise ToolCallError(exc.message) from exc
        if outcome.is_error:
            raise ToolCallError(outcome.error_text)
        parts = list(outcome.text_parts)
        for mime, data in outcome.binary_parts:
            parts.append(f"saved {mime} to {self._save(mime, data)}")
        return "\n".join(parts) if parts else "(no output)"

    def prepare_approval(self, **kwargs: Any) -> ApprovalPreview:
        """`<Title> wants to run <tool title or name>`, one argument per line."""
        lines = [f"{key}: {_short(value)}" for key, value in sorted(kwargs.items())]
        return ApprovalPreview(
            summary=(
                f"{self.connector_title} wants to run "
                f"{self.remote_title or self.remote_name}"
            ),
            detail="\n".join(lines) or None,
            risky=("write", "external"),
        )

    def _save(self, mime: str, data: bytes) -> Path:
        ext = mimetypes.guess_extension(mime) or ".bin"
        name = f"mcp-{self.connector_name}-{secrets.token_hex(4)}{ext}"
        path = self._tmp_dir / name
        atomic_write(path, data)
        return path


def sanitize_description(text: str) -> str:
    """Drop control and invisible formatting characters (bidi overrides,
    zero-width), keep newlines and tabs, and cap the length.
    """
    kept = "".join(
        ch
        for ch in text
        if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf")
    )
    return kept[:MAX_DESCRIPTION_CHARS]


def normalize_schema(schema: Any) -> dict[str, Any]:
    """A parameters schema Agentling accepts: the root must be `type: object`."""
    if not isinstance(schema, dict):
        return dict(_EMPTY_OBJECT_SCHEMA)
    if schema.get("type") == "object":
        return schema
    if "type" not in schema and isinstance(schema.get("properties"), dict):
        return {**schema, "type": "object"}
    return dict(_EMPTY_OBJECT_SCHEMA)


def _short(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    if len(text) <= MAX_PREVIEW_VALUE_CHARS:
        return text
    return text[: MAX_PREVIEW_VALUE_CHARS - 1] + "…"
