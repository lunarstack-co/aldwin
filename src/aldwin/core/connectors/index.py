"""A searchable index of connector tools, so the model can load them on demand.

Only tools the model may use are indexed: the manager hands over exactly the
tools it would otherwise offer, so an `off` tool is never findable.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

MAX_QUERY_CHARS = 200
MIN_PREFIX_CHARS = 3

# Word runs: an acronym, a capitalised or lowercase word, or digits. Splitting
# camelCase and snake_case this way lets "issues" find `listIssues`.
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")

_NAME_WEIGHT = 3
_TITLE_WEIGHT = 2
_DESCRIPTION_WEIGHT = 1


@dataclass(frozen=True)
class IndexedTool:
    """One connector tool as the index knows it."""

    connector: str
    title: str
    name: str
    model_name: str
    description: str
    auto: bool  # runs without asking, under the user's current settings


def tokens(text: str) -> list[str]:
    """Lowercase word tokens of `text`, with camelCase and snake_case split."""
    return [word.lower() for word in _WORD.findall(text)]


def _matches(query_token: str, words: Sequence[str]) -> bool:
    prefix = len(query_token) >= MIN_PREFIX_CHARS
    return any(
        word == query_token or (prefix and word.startswith(query_token))
        for word in words
    )


class ToolIndex:
    """Tools by connector, searchable by name, title and description words."""

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, IndexedTool]] = {}

    def replace(self, connector: str, tools: Sequence[IndexedTool]) -> None:
        """Make `connector`'s entries exactly `tools`."""
        self._tools[connector] = {tool.name: tool for tool in tools}

    def drop(self, connector: str) -> None:
        self._tools.pop(connector, None)

    def connectors(self) -> list[str]:
        return list(self._tools)

    def get(self, connector: str, name: str) -> IndexedTool | None:
        """Look a tool up by its server name or by the model name the prompt lists."""
        entries = self._tools.get(connector, {})
        found = entries.get(name)
        if found is not None:
            return found
        return next((t for t in entries.values() if t.model_name == name), None)

    def names(self, connector: str) -> list[str]:
        return list(self._tools.get(connector, {}))

    def search(
        self, query: str, *, connector: str | None = None, limit: int = 5
    ) -> list[IndexedTool]:
        """The best `limit` matches for `query`, best first; never a non-match."""
        query_tokens = tokens(query[:MAX_QUERY_CHARS])
        if not query_tokens:
            return []
        scored: list[tuple[int, int, str, IndexedTool]] = []
        for order, (name, entries) in enumerate(self._tools.items()):
            if connector is not None and name != connector:
                continue
            for tool in entries.values():
                score = _score(query_tokens, tool)
                if score:
                    scored.append((-score, order, tool.name, tool))
        scored.sort(key=lambda item: item[:3])
        return [item[3] for item in scored[:limit]]


def _score(query_tokens: Sequence[str], tool: IndexedTool) -> int:
    name_words = tokens(tool.name)
    title_words = tokens(tool.title)
    description_words = tokens(tool.description)
    score = 0
    for query_token in query_tokens:
        if _matches(query_token, name_words):
            score += _NAME_WEIGHT
        if _matches(query_token, title_words):
            score += _TITLE_WEIGHT
        if _matches(query_token, description_words):
            score += _DESCRIPTION_WEIGHT
    return score
