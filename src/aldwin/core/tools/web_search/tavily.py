"""Tavily search backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import httpx

from aldwin.core.settings import SecretsConfig
from aldwin.core.tools.web_search.hosted import (
    HostedSearchProvider,
    load_search_secrets,
    map_hits,
    resolve_search_api_key,
)
from aldwin.core.tools.web_search.shared import SearchResult

TAVILY_SEARCH_URL: Final[str] = "https://api.tavily.com/search"
TAVILY_USAGE_URL: Final[str] = "https://api.tavily.com/usage"


def resolve_tavily_key(
    home: Path | None,
    secrets: SecretsConfig | None,
) -> str | None:
    """Resolve the Tavily API key from environment first, then secrets."""
    return resolve_search_api_key("tavily", load_search_secrets(home, secrets))


class TavilyProvider(HostedSearchProvider):
    """Tavily Search API (basic depth)."""

    provider_id = "tavily"
    name = "Tavily"
    default_endpoint = TAVILY_SEARCH_URL
    check_url = TAVILY_USAGE_URL
    # /usage answers a bad key at once but can take 5 to 20 s for a good one.
    check_timeout = 30.0
    check_note = "key check can take up to 20 seconds"
    signup_url = "https://app.tavily.com"

    @classmethod
    def setup_hint(cls) -> str:
        return "Connect it with /search or aldwin setup, or set TAVILY_API_KEY."

    async def _send(self, query: str, limit: int) -> httpx.Response:
        return await self._request(
            "POST",
            headers=self._auth_headers(),
            body={"query": query, "max_results": limit, "search_depth": "basic"},
        )

    def _parse(self, data: dict[str, Any], limit: int) -> list[SearchResult]:
        hits = data.get("results")
        if not isinstance(hits, list):
            raise self._unexpected()
        return map_hits(
            hits, limit, lambda hit: hit.get("content") or hit.get("snippet")
        )
