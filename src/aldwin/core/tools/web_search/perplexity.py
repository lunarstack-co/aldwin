"""Perplexity Search API backend (structured results, not chat completions)."""

from __future__ import annotations

from typing import Any, Final

import httpx

from aldwin.core.tools.web_search.hosted import HostedSearchProvider, map_hits
from aldwin.core.tools.web_search.shared import SearchResult

PERPLEXITY_SEARCH_URL: Final[str] = "https://api.perplexity.ai/search"
PERPLEXITY_CHECK_URL: Final[str] = "https://api.perplexity.ai/v1/models"


class PerplexityProvider(HostedSearchProvider):
    """Perplexity Search API."""

    provider_id = "perplexity"
    name = "Perplexity"
    default_endpoint = PERPLEXITY_SEARCH_URL
    check_url = PERPLEXITY_CHECK_URL
    signup_url = "https://www.perplexity.ai/settings/api"

    async def _send(self, query: str, limit: int) -> httpx.Response:
        return await self._request(
            "POST",
            headers=self._auth_headers(),
            body={"query": query, "max_results": limit},
        )

    def _parse(self, data: dict[str, Any], limit: int) -> list[SearchResult]:
        hits = data.get("results")
        if not isinstance(hits, list):
            raise self._unexpected()
        return map_hits(hits, limit, lambda hit: hit.get("snippet"))
