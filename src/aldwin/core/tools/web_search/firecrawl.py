"""Firecrawl search backend (search only; page fetching stays in web_fetch)."""

from __future__ import annotations

from typing import Any, Final

import httpx

from aldwin.core.tools.web_search.hosted import (
    MAX_ERROR_DETAIL_CHARS,
    HostedSearchProvider,
    map_hits,
)
from aldwin.core.tools.web_search.shared import (
    SearchProviderError,
    SearchResult,
    scrub_key,
    truncate_snippet,
)

FIRECRAWL_SEARCH_URL: Final[str] = "https://api.firecrawl.dev/v2/search"
FIRECRAWL_CHECK_URL: Final[str] = "https://api.firecrawl.dev/v2/team/credit-usage"


class FirecrawlProvider(HostedSearchProvider):
    """Firecrawl v2 search."""

    provider_id = "firecrawl"
    name = "Firecrawl"
    default_endpoint = FIRECRAWL_SEARCH_URL
    check_url = FIRECRAWL_CHECK_URL
    # Only 401/403 mean a bad key; "not found" means the key was accepted.
    check_ok_statuses = frozenset({200, 404})
    signup_url = "https://www.firecrawl.dev/app/api-keys"

    async def _send(self, query: str, limit: int) -> httpx.Response:
        return await self._request(
            "POST",
            headers=self._auth_headers(),
            body={"query": query, "limit": limit},
        )

    def _parse(self, data: dict[str, Any], limit: int) -> list[SearchResult]:
        if data.get("success") is False:
            error = truncate_snippet(
                scrub_key(str(data.get("error") or "unknown error"), self.secret),
                MAX_ERROR_DETAIL_CHARS,
            )
            raise SearchProviderError(f"Firecrawl search failed: {error}")
        payload = data.get("data")
        # v2 groups hits by source ({"web": [...]}); older responses use a flat list.
        hits = payload.get("web", []) if isinstance(payload, dict) else payload
        if not isinstance(hits, list):
            raise self._unexpected()
        return map_hits(hits, limit, lambda hit: hit.get("description"))
