"""SearXNG search backend and local auto-detection."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Final
from urllib.parse import urljoin

import httpx

from aldwin.core.settings import AldwinConfig, load_config
from aldwin.core.tools.web_search.shared import (
    DEFAULT_LIMIT,
    SEARCH_TIMEOUT_SECONDS,
    SearchProviderError,
    SearchResult,
)

# Serialize + space local SearXNG calls so upstream engines (Brave/DDG/…)
# are less likely to CAPTCHA or suspend the instance IP.
SEARXNG_MIN_INTERVAL_SECONDS: Final[float] = 1.5
_searxng_pace_lock: asyncio.Lock | None = None
_searxng_pace_loop: asyncio.AbstractEventLoop | None = None
_searxng_last_search_at: float = 0.0

SEARXNG_NOT_SET_UP_MESSAGE: Final[str] = (
    "Web search isn't set up. Set search.searxng_url (or SEARXNG_URL / "
    "SEARXNG_BASE_URL) to a JSON-enabled SearXNG instance."
)

_LOCAL_SEARXNG_CANDIDATES: Final[tuple[str, ...]] = (
    "http://127.0.0.1:8080",
    "http://localhost:8080",
)
# Keep this short: detection uses /config, not a full search.
_SEARXNG_DETECT_TIMEOUT: Final[float] = 1.0
_SEARXNG_DETECT_MISS_TTL: Final[float] = 30.0
_UNSET: Final[object] = object()
_detected_searxng_url: object = _UNSET
_detected_searxng_at: float = 0.0


def searxng_search_endpoint(base_url: str) -> str:
    """Join a SearXNG base URL to ``/search`` without doubling the path."""
    cleaned = base_url.strip()
    if not cleaned:
        raise ValueError("SearXNG base URL must not be empty.")
    trimmed = cleaned.rstrip("/")
    if trimmed.endswith("/search"):
        return trimmed
    # urljoin keeps scheme/host and replaces a trailing path segment correctly.
    return urljoin(trimmed + "/", "search")


def clear_searxng_detection_cache() -> None:
    """Drop the local SearXNG auto-detect cache (tests and ``/search`` refresh)."""
    global _detected_searxng_url, _detected_searxng_at
    _detected_searxng_url = _UNSET
    _detected_searxng_at = 0.0


def clear_searxng_pace_state() -> None:
    """Reset SearXNG pacing (tests). Does not cancel an in-flight lock wait."""
    global _searxng_last_search_at, _searxng_pace_lock, _searxng_pace_loop
    _searxng_last_search_at = 0.0
    _searxng_pace_lock = None
    _searxng_pace_loop = None


def _searxng_lock() -> asyncio.Lock:
    """Return a pace lock bound to the current event loop."""
    global _searxng_pace_lock, _searxng_pace_loop
    loop = asyncio.get_running_loop()
    if _searxng_pace_lock is None or _searxng_pace_loop is not loop:
        _searxng_pace_lock = asyncio.Lock()
        _searxng_pace_loop = loop
    return _searxng_pace_lock


def _upstream_rate_limit_message(data: dict[str, Any]) -> str | None:
    """Return an error when engines are blocked and no usable results remain."""
    raw_results = data.get("results")
    if isinstance(raw_results, list) and any(
        isinstance(item, dict) and str(item.get("url") or "").strip()
        for item in raw_results
    ):
        return None

    engines = data.get("unresponsive_engines")
    if not isinstance(engines, list) or not engines:
        return None

    blocked: list[str] = []
    for item in engines:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        name = str(item[0]).strip() or "engine"
        reason = str(item[1]).strip()
        lowered = reason.lower()
        if any(
            token in lowered
            for token in (
                "too many",
                "rate",
                "captcha",
                "suspended",
                "blocked",
                "banned",
            )
        ):
            blocked.append(f"{name}: {reason}" if reason else name)
    if not blocked:
        return None

    detail = "; ".join(blocked[:4])
    return (
        "Search engines are rate-limited or blocked "
        f"({detail}). Wait a minute, use one broader query, "
        "or ask the user to switch search provider."
    )


def _probe_searxng_json(
    base_url: str, *, timeout: float = _SEARXNG_DETECT_TIMEOUT
) -> bool:
    """Return True when ``base_url`` looks like a SearXNG instance.

    Requires ``/config`` JSON with SearXNG-specific keys so a random service on
    port 8080 is not treated as search.
    """
    cleaned = base_url.strip().rstrip("/")
    if not cleaned:
        return False
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(f"{cleaned}/config")
            if response.status_code != 200:
                return False
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    return isinstance(data, dict) and "engines" in data and "instance_name" in data


def detect_local_searxng(*, force: bool = False) -> str | None:
    """Probe common local SearXNG URLs; cache hits and short-lived misses."""
    global _detected_searxng_url, _detected_searxng_at

    now = time.monotonic()
    if not force and _detected_searxng_url is not _UNSET:
        if _detected_searxng_url is not None:
            return str(_detected_searxng_url)
        if now - _detected_searxng_at < _SEARXNG_DETECT_MISS_TTL:
            return None

    for candidate in _LOCAL_SEARXNG_CANDIDATES:
        if _probe_searxng_json(candidate):
            _detected_searxng_url = candidate
            _detected_searxng_at = now
            return candidate

    _detected_searxng_url = None
    _detected_searxng_at = now
    return None


def resolve_searxng_url(
    home: Path | None = None,
    *,
    config: AldwinConfig | None = None,
    detect_local: bool = True,
) -> str | None:
    """Resolve SearXNG base: config → env → local auto-detect (default ports)."""
    cfg = config
    if cfg is None and home is not None:
        cfg = load_config(home)
    if cfg is not None:
        from_config = (cfg.search.searxng_url or "").strip()
        if from_config:
            return from_config
    for env_name in ("SEARXNG_URL", "SEARXNG_BASE_URL"):
        from_env = os.environ.get(env_name, "").strip()
        if from_env:
            return from_env
    if detect_local:
        return detect_local_searxng()
    return None


class SearxngProvider:
    """SearXNG JSON search backend using plain httpx.

    The configured base URL may be localhost or a private network address; that
    is intentional for self-hosted metasearch and is not subject to web_fetch
    private-network blocking.
    """

    name = "SearXNG"
    is_local = True
    secret = ""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = SEARCH_TIMEOUT_SECONDS,
        min_interval: float = SEARXNG_MIN_INTERVAL_SECONDS,
    ) -> None:
        cleaned = base_url.strip()
        if not cleaned:
            raise ValueError("SearXNG base URL must not be empty.")
        self._endpoint = searxng_search_endpoint(cleaned)
        self._client = client
        self._timeout = timeout
        self._min_interval = max(0.0, float(min_interval))

    async def _get_search(
        self,
        client: httpx.AsyncClient,
        query: str,
    ) -> httpx.Response:
        return await client.get(
            self._endpoint,
            params={"q": query, "format": "json", "pageno": 1},
            timeout=self._timeout,
        )

    async def _paced_get_search(
        self,
        client: httpx.AsyncClient,
        query: str,
    ) -> httpx.Response:
        """Serialize SearXNG HTTP calls and enforce a minimum spacing."""
        global _searxng_last_search_at
        async with _searxng_lock():
            if self._min_interval > 0:
                elapsed = time.monotonic() - _searxng_last_search_at
                wait_for = self._min_interval - elapsed
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
            _searxng_last_search_at = time.monotonic()
            return await self._get_search(client, query)

    async def search(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
    ) -> list[SearchResult]:
        """Query SearXNG JSON search and map ``results[]`` to ``SearchResult``."""
        try:
            if self._client is not None:
                response = await self._paced_get_search(self._client, query)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await self._paced_get_search(client, query)
        except httpx.TimeoutException as exc:
            raise SearchProviderError("request timed out") from exc
        except httpx.HTTPError as exc:
            reason = " ".join(str(exc).split()) or type(exc).__name__
            raise SearchProviderError(reason) from exc

        if response.status_code == 403:
            raise SearchProviderError(
                "SearXNG rejected the request (HTTP 403). Enable JSON output on "
                "the instance (add 'json' to search.formats / allow format=json)."
            )
        if response.status_code == 429:
            raise SearchProviderError("SearXNG rate limit reached (HTTP 429)")
        if response.status_code >= 400:
            raise SearchProviderError(f"HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderError("invalid JSON response from SearXNG") from exc

        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise SearchProviderError("unexpected response format from SearXNG")

        rate_limited = _upstream_rate_limit_message(data)
        if rate_limited is not None:
            raise SearchProviderError(rate_limited)

        raw_results = [item for item in data["results"] if isinstance(item, dict)]
        if any("score" in item for item in raw_results):

            def _score(item: dict[str, Any]) -> float:
                raw_score = item.get("score")
                if raw_score is None:
                    return float("-inf")
                try:
                    return float(raw_score)
                except (TypeError, ValueError):
                    return float("-inf")

            raw_results.sort(key=_score, reverse=True)

        results: list[SearchResult] = []
        for raw in raw_results[:limit]:
            url = str(raw.get("url") or "").strip()
            if not url:
                continue
            title = " ".join(str(raw.get("title") or "").split()) or url
            snippet = " ".join(str(raw.get("content") or "").split())
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        return results
