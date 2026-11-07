"""Parallel Search API backend."""

from __future__ import annotations

from typing import Any, Final

import httpx

from aldwin.core.tools.web_search.hosted import HostedSearchProvider, map_hits
from aldwin.core.tools.web_search.shared import SearchResult

PARALLEL_SEARCH_URL: Final[str] = "https://api.parallel.ai/v1/search"
# Looking up a run that does not exist checks the key without starting any work.
PARALLEL_CHECK_URL: Final[str] = (
    "https://api.parallel.ai/v1/tasks/runs/trun_aldwin_key_check"
)
# Parallel caps each entry in search_queries at 200 characters.
PARALLEL_MAX_QUERY_CHARS: Final[int] = 200
PARALLEL_EXCERPT_CHARS: Final[int] = 600


def _first_excerpt(hit: dict[str, Any]) -> object:
    excerpts = hit.get("excerpts")
    if isinstance(excerpts, list):
        for item in excerpts:
            if isinstance(item, str) and item.strip():
                return item
    return ""


class ParallelProvider(HostedSearchProvider):
    """Parallel Search API (``x-api-key`` auth, not Bearer)."""

    provider_id = "parallel"
    name = "Parallel"
    default_endpoint = PARALLEL_SEARCH_URL
    check_url = PARALLEL_CHECK_URL
    # A valid key gets "not found" or a validation error for the made-up run id.
    check_ok_statuses = frozenset({200, 400, 404, 422})
    signup_url = "https://platform.parallel.ai"

    def _auth_headers(self) -> dict[str, str]:
        return {"x-api-key": self.secret}

    async def _send(self, query: str, limit: int) -> httpx.Response:
        return await self._request(
            "POST",
            headers=self._auth_headers(),
            # The schema rejects unknown top-level fields, so limits live here.
            body={
                "search_queries": [query[:PARALLEL_MAX_QUERY_CHARS]],
                "advanced_settings": {
                    "max_results": limit,
                    "excerpt_settings": {
                        "max_chars_per_result": PARALLEL_EXCERPT_CHARS
                    },
                },
            },
        )

    def _parse(self, data: dict[str, Any], limit: int) -> list[SearchResult]:
        hits = data.get("results")
        if not isinstance(hits, list):
            raise self._unexpected()
        return map_hits(hits, limit, _first_excerpt)
