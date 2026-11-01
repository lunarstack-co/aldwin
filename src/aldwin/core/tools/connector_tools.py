"""Tools that reach the connector manager through callables the host injects.

`core` never imports `host`, so the host hands these tools what they need.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.connectors.index import IndexedTool, ToolIndex
from aldwin.core.connectors.mcp_tool import sanitize_description

MAX_NAMES = 20
MAX_RESULT_DESCRIPTION_CHARS = 160


@dataclass(frozen=True)
class LoadResult:
    """What a load did: the tools now offered, those dropped to make room, and
    those that did not fit."""

    loaded: list[IndexedTool] = field(default_factory=list)
    evicted: list[str] = field(default_factory=list)
    skipped: list[IndexedTool] = field(default_factory=list)


LoadFn = Callable[[Sequence[IndexedTool]], LoadResult]


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
                    "Exact tool names on that connector, e.g. ['list_issues']. "
                    "Needs connector."
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


def build_find_connector_tools_tool(*, index: ToolIndex, load: LoadFn) -> Tool:
    return FindConnectorToolsTool(index=index, load=load)
