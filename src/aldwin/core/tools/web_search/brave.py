"""Brave Search web results backend."""

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

BRAVE_SEARCH_URL: Final[str] = "https://api.search.brave.com/res/v1/web/search"
# Brave answers a bad key with 422 and this code, not 401.
BRAVE_INVALID_TOKEN_CODE: Final[str] = "SUBSCRIPTION_TOKEN_INVALID"


def _is_invalid_token(response: httpx.Response) -> bool:
    if response.status_code != 422:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    error = body.get("error") if isinstance(body, dict) else None
    return isinstance(error, dict) and error.get("code") == BRAVE_INVALID_TOKEN_CODE


class BraveProvider(HostedSearchProvider):
    """Brave Search Web API (web results, not llm-context)."""

    provider_id = "brave"
    name = "Brave"
    default_endpoint = BRAVE_SEARCH_URL
    env_aliases = ("BRAVE_SEARCH_API_KEY",)
    # Brave has no free endpoint that checks a key, so the check is one search.
    check_url = BRAVE_SEARCH_URL
    check_note = "key check uses one Brave query"
    signup_url = "https://api-dashboard.search.brave.com"

    def _auth_headers(self) -> dict[str, str]:
        return {"X-Subscription-Token": self.secret, "Accept": "application/json"}

    async def check_key(self) -> KeyCheckResult:
        try:
            response = await self._request(
                "GET",
                headers=self._auth_headers(),
                params={"q": "aldwin", "count": 1},
                timeout=self.check_timeout,
            )
        except SearchProviderError as exc:
            return KeyCheckResult(KeyCheck.UNREACHABLE, str(exc))
        if _is_invalid_token(response):
            return KeyCheckResult(KeyCheck.REJECTED, "HTTP 422")
        return self._classify_check(response)

    async def _send(self, query: str, limit: int) -> httpx.Response:
        return await self._request(
            "GET",
            headers=self._auth_headers(),
            params={"q": query, "count": limit},
        )

    def _check_status(self, response: httpx.Response) -> None:
        if _is_invalid_token(response):
            raise SearchProviderError(
                "invalid or unauthorized Brave API key (HTTP 422)"
            )
        super()._check_status(response)

    def _parse(self, data: dict[str, Any], limit: int) -> list[SearchResult]:
        # Brave omits the "web" block entirely when nothing matched.
        web = data.get("web")
        if web is None:
            return []
        hits = web.get("results", []) if isinstance(web, dict) else None
        if not isinstance(hits, list):
            raise self._unexpected()
        return map_hits(hits, limit, lambda hit: hit.get("description"))
