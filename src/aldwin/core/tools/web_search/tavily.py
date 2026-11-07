"""Tavily search backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import httpx

from aldwin.core.settings import SecretsConfig, load_secrets, resolve_key
from aldwin.core.tools.web_search.shared import (
    DEFAULT_LIMIT,
    SEARCH_TIMEOUT_SECONDS,
    SearchProviderError,
    SearchResult,
    scrub_key,
)

TAVILY_SEARCH_URL: Final[str] = "https://api.tavily.com/search"


def resolve_tavily_key(
    home: Path | None,
    secrets: SecretsConfig | None,
) -> str | None:
    """Resolve the Tavily API key from environment first, then `secrets` or disk."""
    resolved = resolve_key("tavily", home)
    if resolved.key:
        return resolved.key
    if secrets is not None:
        direct = secrets.get_secret("tavily")
        if direct:
            return direct
    if home is not None:
        loaded = load_secrets(home)
        return loaded.get_secret("tavily")
    return None


class TavilyProvider:
    """Tavily Search API implementation of SearchProvider using httpx."""

    name = "Tavily"
    is_local = False

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = SEARCH_TIMEOUT_SECONDS,
        endpoint: str = TAVILY_SEARCH_URL,
    ) -> None:
        cleaned_key = api_key.strip()
        if not cleaned_key:
            raise ValueError("Tavily API key must not be empty.")
        self._api_key = cleaned_key
        self.secret = cleaned_key
        self._client = client
        self._timeout = timeout
        self._endpoint = endpoint

    async def _post_search(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        return await client.post(
            self._endpoint,
            headers=headers,
            json=payload,
            timeout=self._timeout,
        )

    async def search(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
    ) -> list[SearchResult]:
        """Query Tavily's basic search endpoint and map results."""
        payload: dict[str, Any] = {
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
        }
        try:
            if self._client is not None:
                response = await self._post_search(self._client, payload)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await self._post_search(client, payload)
        except httpx.TimeoutException as exc:
            raise SearchProviderError("request timed out") from exc
        except httpx.HTTPError as exc:
            reason = scrub_key(str(exc), self._api_key) or type(exc).__name__
            raise SearchProviderError(reason) from exc

        if response.status_code in {401, 403}:
            raise SearchProviderError(
                f"invalid or unauthorized Tavily API key (HTTP {response.status_code})"
            )
        if response.status_code == 429:
            raise SearchProviderError(
                "Tavily rate limit or monthly credit limit reached (HTTP 429)"
            )
        if response.status_code >= 400:
            raise SearchProviderError(f"HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderError("invalid JSON response from Tavily") from exc

        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise SearchProviderError("unexpected response format from Tavily")

        results: list[SearchResult] = []
        for raw in data["results"][:limit]:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            title = " ".join(str(raw.get("title") or "").split()) or url
            snippet = " ".join(
                str(raw.get("content") or raw.get("snippet") or "").split()
            )
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        return results
