"""Exa search backend."""

from __future__ import annotations

from typing import Any, Final

import httpx

from aldwin.core.tools.web_search.hosted import (
    HostedSearchProvider,
    KeyCheck,
    KeyCheckResult,
    map_hits,
)
from aldwin.core.tools.web_search.shared import SearchProviderError, SearchResult

EXA_SEARCH_URL: Final[str] = "https://api.exa.ai/search"
# Listing websets needs a valid key and creates nothing.
EXA_CHECK_URL: Final[str] = "https://api.exa.ai/websets/v0/websets"


def _exa_snippet(hit: dict[str, Any]) -> object:
    highlights = hit.get("highlights")
    if isinstance(highlights, list):
        joined = " ".join(item for item in highlights if isinstance(item, str))
        if joined.strip():
            return joined
    return hit.get("text")


class ExaProvider(HostedSearchProvider):
    """Exa Search API with highlights for snippets."""

    provider_id = "exa"
    name = "Exa"
    default_endpoint = EXA_SEARCH_URL
    check_url = EXA_CHECK_URL
    signup_url = "https://dashboard.exa.ai"
    check_note = "key check may use one Exa query"

    def _auth_headers(self) -> dict[str, str]:
        # Exa's primary scheme; some keys are refused as a Bearer token.
        return {"x-api-key": self.secret}

    async def check_key(self) -> KeyCheckResult:
        result = await super().check_key()
        if result.outcome is not KeyCheck.REJECTED:
            return result
        # Keys without websets access can fail the list; search is the real test.
        try:
            response = await self._send("aldwin", 1)
        except SearchProviderError as exc:
            return KeyCheckResult(KeyCheck.UNREACHABLE, str(exc))
        return self._classify_check(response)

    async def _send(self, query: str, limit: int) -> httpx.Response:
        return await self._request(
            "POST",
            headers=self._auth_headers(),
            # Highlights are query-relevant excerpts; full "text" can be a whole page.
            body={
                "query": query,
                "numResults": limit,
                "type": "auto",
                "contents": {"highlights": True},
            },
        )

    def _parse(self, data: dict[str, Any], limit: int) -> list[SearchResult]:
        hits = data.get("results")
        if not isinstance(hits, list):
            raise self._unexpected()
        return map_hits(hits, limit, _exa_snippet)
