"""Tools that reach the connector manager through callables the host injects.

`core` never imports `host`, so the host hands these tools what they need.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.connectors.errors import ConnectorError
from aldwin.core.connectors.index import IndexedTool, ToolIndex
from aldwin.core.connectors.mcp_tool import sanitize_description
from aldwin.core.connectors.proposal import (
    ConnectorProposal,
    ProposeOutcome,
    format_outcome,
)
from aldwin.core.policy import ApprovalPreview

MAX_NAMES = 20
MAX_RESULT_DESCRIPTION_CHARS = 160
# The add flow's own sign-in wait is 5 minutes; this bounds the whole proposal.
PROPOSE_CAP_S: Final[float] = 360.0
MAX_PREVIEWS: Final[int] = 8


@dataclass(frozen=True)
class LoadResult:
    """What a load did: the tools now offered, those dropped to make room, and
    those that did not fit."""

    loaded: list[IndexedTool] = field(default_factory=list)
    evicted: list[str] = field(default_factory=list)
    skipped: list[IndexedTool] = field(default_factory=list)


LoadFn = Callable[[Sequence[IndexedTool]], LoadResult]
ResolveFn = Callable[[dict[str, Any]], ConnectorProposal]
# The preview the user approves, plus the launch digest of a local command.
PreviewFn = Callable[[ConnectorProposal], tuple[ApprovalPreview, str | None]]
ProposeFn = Callable[[ConnectorProposal, str | None], Awaitable[ProposeOutcome]]


class FindConnectorToolsTool(Tool):
    """Find connector tools by words or by name and load them into the session."""

    name = "find_connector_tools"
    description = (
        "Load tools from connected apps (connectors) so you can call them. "
        "Give a query to search every connected app, or a connector and the "
        "names of the tools you want (the names are listed under <connectors>). "
        "Loaded tools stay available; call them in your next step."
    )
    parallel_safe = False
    timeout = None
    max_output_chars = 4_000
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Words describing what you need, e.g. 'list issues'.",
            },
            "connector": {
                "type": "string",
                "description": "Limit to one connector by its name, e.g. 'linear'.",
            },
            "names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Exact tool names on that connector as listed under "
                    "<connectors>, e.g. ['mcp__linear__list_issues']. Needs connector."
                ),
            },
        },
        "additionalProperties": False,
    }

    def __init__(self, *, index: ToolIndex, load: LoadFn) -> None:
        self._index = index
        self._load = load

    async def forward(  # type: ignore[override]
        self,
        query: str = "",
        connector: str = "",
        names: Sequence[str] | None = None,
    ) -> str:
        names = list(names or [])
        query = query.strip()
        connector = connector.strip()
        if names and not connector:
            raise ToolCallError("names needs connector.")
        if len(names) > MAX_NAMES:
            raise ToolCallError(f"At most {MAX_NAMES} names.")
        if not names and not query:
            raise ToolCallError(
                "Give a query, or a connector and the names of the tools you want."
            )
        apps = ", ".join(self._index.connectors()) or "none"
        if connector and connector not in self._index.connectors():
            return f"No connector named {connector}. Connected apps: {apps}."

        lines: list[str] = []
        wanted: list[IndexedTool] = []
        if names:
            for name in names:
                tool = self._index.get(connector, name)
                if tool is None:
                    lines.append(f"No tool named {name} on {connector}.")
                else:
                    wanted.append(tool)
        else:
            wanted = self._index.search(query, connector=connector or None)
            if not wanted:
                return f'No connector tool matches "{query}". Connected apps: {apps}.'
        if not wanted:
            return "\n".join(lines)

        result = self._load(wanted)
        lines.extend(_describe(tool) for tool in result.loaded)
        lines.extend(
            f"{tool.model_name}: not loaded (over the limit of loaded tools)"
            for tool in result.skipped
        )
        if result.evicted:
            lines.append(f"Unloaded to make room: {', '.join(result.evicted)}.")
        lines.append(f"Loaded {len(result.loaded)} tools. You can call them now.")
        return "\n".join(lines)


def _describe(tool: IndexedTool) -> str:
    text = sanitize_description(tool.description).replace("\n", " ").strip()
    text = text[:MAX_RESULT_DESCRIPTION_CHARS] or tool.title or tool.name
    how = "runs automatically" if tool.auto else "asks the user first"
    return f"{tool.model_name}: {text} ({how})"


class ProposeConnectorTool(Tool):
    """Propose adding a connector, or turning a saved one on. Always asks."""

    name = "propose_connector"
    description = (
        "Propose adding a connector (a connected app) when the user asks you to "
        "connect one. Give exactly one of: name (an app listed as available under "
        "<connectors>, or a saved connector), url (an MCP server address) or "
        "command (a local MCP server's command line). The user approves it and "
        "signs in privately; you get back one line saying what happened. Never ask "
        "the user to type or paste a token or password into the chat."
    )
    parallel_safe = False
    timeout = 400.0
    max_output_chars = 500
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "An available app, e.g. 'linear', or a saved connector.",
            },
            "url": {
                "type": "string",
                "description": "An MCP server address, e.g. 'https://mcp.example.com/mcp'.",
            },
            "command": {
                "type": "string",
                "description": "A local server's command line, e.g. 'npx -y pkg@1'.",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Arguments for command, if not in the command line.",
            },
        },
        "additionalProperties": False,
    }

    def __init__(
        self, *, resolve: ResolveFn, preview: PreviewFn, propose: ProposeFn
    ) -> None:
        self._resolve = resolve
        self._preview = preview
        self._propose = propose
        # What the user was last shown: arguments, fingerprint, launch digest.
        self._previewed: dict[str, tuple[str, str | None]] = {}

    def prepare_approval(self, **args: Any) -> ApprovalPreview:
        proposal = self._proposal(args)
        try:
            preview, digest = self._preview(proposal)
        except ConnectorError as exc:
            raise ToolCallError(exc.message) from None
        key = _args_key(args)
        self._previewed.pop(key, None)
        self._previewed[key] = (proposal.fingerprint(), digest)
        # Declined proposals are never run; keep only the most recent few.
        while len(self._previewed) > MAX_PREVIEWS:
            del self._previewed[next(iter(self._previewed))]
        return preview

    async def forward(self, **args: Any) -> str:
        shown = self._previewed.pop(_args_key(args), None)
        try:
            proposal = self._proposal(args)
        except ToolCallError:
            if shown is None:
                raise
            return format_outcome(ProposeOutcome("changed", ""))
        if shown is None or shown[0] != proposal.fingerprint():
            return format_outcome(ProposeOutcome("changed", proposal.name))
        try:
            async with asyncio.timeout(PROPOSE_CAP_S):
                outcome = await self._propose(proposal, shown[1])
        except TimeoutError:
            outcome = ProposeOutcome(
                "sign_in_failed", proposal.name, reason="timed out"
            )
        return format_outcome(outcome)

    def _proposal(self, args: dict[str, Any]) -> ConnectorProposal:
        try:
            return self._resolve(args)
        except ConnectorError as exc:
            raise ToolCallError(exc.message) from None


def build_propose_connector_tool(
    *, resolve: ResolveFn, preview: PreviewFn, propose: ProposeFn
) -> Tool:
    return ProposeConnectorTool(resolve=resolve, preview=preview, propose=propose)


def _args_key(args: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in args.items() if v}, sort_keys=True)


def build_find_connector_tools_tool(*, index: ToolIndex, load: LoadFn) -> Tool:
    return FindConnectorToolsTool(index=index, load=load)
