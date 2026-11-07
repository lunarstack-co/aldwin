"""Shared transport, error mapping, and key lookup for hosted search APIs."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Final

import httpx

from aldwin.core.settings import SecretsConfig, load_secrets, normalize_key_field
from aldwin.core.tools.web_search.shared import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
    SEARCH_TIMEOUT_SECONDS,
    SearchProviderError,
    SearchResult,
    scrub_key,
    truncate_snippet,
)

MAX_ERROR_DETAIL_CHARS: Final[int] = 300
MAX_ERROR_BODY_CHARS: Final[int] = 4_000
KEY_CHECK_TIMEOUT_SECONDS: Final[float] = 10.0


class KeyCheck(StrEnum):
    """Outcome of checking an API key against the provider."""

    OK = "ok"
    REJECTED = "rejected"
    NO_CREDITS = "no_credits"
    RATE_LIMITED = "rate_limited"
    UNREACHABLE = "unreachable"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class KeyCheckResult:
    """A key check outcome plus a short reason such as ``HTTP 401``."""

    outcome: KeyCheck
    detail: str = ""


def load_search_secrets(
    home: Path | None,
    secrets: SecretsConfig | None,
) -> tuple[SecretsConfig, ...]:
    """Return the secrets to search: ``secrets.yaml`` when there is a home.

    The file is the source of truth so keys connected or disconnected with
    ``/search`` take effect on the next search; ``secrets`` (often a snapshot
    taken at session start) is used only when there is no home folder.
    """
    if home is not None:
        return (load_secrets(home),)
    if secrets is not None:
        return (secrets,)
    return ()


def resolve_search_api_key(
    provider: str,
    sources: Sequence[SecretsConfig],
    *,
    env_aliases: tuple[str, ...] = (),
) -> str | None:
    """Return the key: ``<PROVIDER>_API_KEY``, then aliases, then ``sources``."""
    field = normalize_key_field(provider)
    for env_name in (field.upper(), *env_aliases):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    for source in sources:
        stored = source.get_secret(field)
        if stored:
            return stored
    return None


def map_hits(
    hits: list[Any],
    limit: int,
    snippet: Callable[[dict[str, Any]], object],
) -> list[SearchResult]:
    """Map vendor hits with ``title``/``url`` keys to at most ``limit`` results."""
    results: list[SearchResult] = []
    for hit in hits:
        if len(results) >= limit:
            break
        if not isinstance(hit, dict):
            continue
        url = str(hit.get("url") or "").strip()
        if not url:
            continue
        title = " ".join(str(hit.get("title") or "").split()) or url
        text = " ".join(str(snippet(hit) or "").split())
        results.append(SearchResult(title=title, url=url, snippet=text))
    return results


class HostedSearchProvider:
    """Base for keyed search APIs: one request, uniform errors, mapped hits.

    Subclasses set the class attributes and implement ``_send`` and ``_parse``.
    """

    provider_id: ClassVar[str]
    name: ClassVar[str]
    default_endpoint: ClassVar[str]
    env_aliases: ClassVar[tuple[str, ...]] = ()
    is_local: ClassVar[bool] = False
    # A free endpoint that needs a valid key; checking never runs a search.
    check_url: ClassVar[str]
    check_ok_statuses: ClassVar[frozenset[int]] = frozenset({200})
    check_note: ClassVar[str | None] = None
    check_timeout: ClassVar[float] = KEY_CHECK_TIMEOUT_SECONDS
    signup_url: ClassVar[str]

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = SEARCH_TIMEOUT_SECONDS,
        endpoint: str | None = None,
    ) -> None:
        cleaned = api_key.strip()
        if not cleaned:
            raise ValueError(f"{self.name} API key must not be empty.")
        self.secret = cleaned
        self._client = client
        self._timeout = timeout
        self._endpoint = endpoint or self.default_endpoint

    @classmethod
    def env_var(cls) -> str:
        """Primary environment variable holding this provider's key."""
        return normalize_key_field(cls.provider_id).upper()

    @classmethod
    def setup_hint(cls) -> str:
        """One sentence telling the user how to connect this provider."""
        names = cls.env_var()
        if cls.env_aliases:
            names += f" (or {', '.join(cls.env_aliases)})"
        return f"Connect it with /search, or set {names}."

    async def search(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
    ) -> list[SearchResult]:
        """Return up to ``limit`` results for ``query``."""
        safe_limit = max(MIN_LIMIT, min(MAX_LIMIT, limit))
        response = await self._send(query, safe_limit)
        self._check_status(response)
        return self._parse(self._json_object(response), safe_limit)

    async def _send(self, query: str, limit: int) -> httpx.Response:
        raise NotImplementedError

    def _parse(self, data: dict[str, Any], limit: int) -> list[SearchResult]:
        raise NotImplementedError

    async def check_key(self) -> KeyCheckResult:
        """Ask the provider whether this key works, without saving anything."""
        try:
            response = await self._request(
                "GET",
                url=self.check_url,
                headers=self._auth_headers(),
                timeout=self.check_timeout,
            )
        except SearchProviderError as exc:
            return KeyCheckResult(KeyCheck.UNREACHABLE, str(exc))
        return self._classify_check(response)

    def _classify_check(self, response: httpx.Response) -> KeyCheckResult:
        status = response.status_code
        detail = f"HTTP {status}"
        if status in self.check_ok_statuses:
            return KeyCheckResult(KeyCheck.OK, detail)
        if status in {401, 403}:
            return KeyCheckResult(KeyCheck.REJECTED, detail)
        if status == 402:
            return KeyCheckResult(KeyCheck.NO_CREDITS, detail)
        if status == 429:
            return KeyCheckResult(KeyCheck.RATE_LIMITED, detail)
        if status >= 500:
            return KeyCheckResult(KeyCheck.UNREACHABLE, detail)
        return KeyCheckResult(KeyCheck.UNEXPECTED, detail)

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.secret}"}

    def _unexpected(self) -> SearchProviderError:
        return SearchProviderError(f"unexpected response format from {self.name}")

    async def _request(
        self,
        method: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        url: str | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        target = url or self._endpoint
        limit = self._timeout if timeout is None else timeout
        try:
            if self._client is not None:
                return await self._client.request(
                    method,
                    target,
                    headers=headers,
                    params=params,
                    json=body,
                    timeout=limit,
                )
            async with httpx.AsyncClient(timeout=limit) as client:
                return await client.request(
                    method, target, headers=headers, params=params, json=body
                )
        except httpx.TimeoutException as exc:
            raise SearchProviderError("request timed out") from exc
        except httpx.HTTPError as exc:
            reason = scrub_key(str(exc), self.secret) or type(exc).__name__
            raise SearchProviderError(reason) from exc

    def _check_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status in {401, 403}:
            raise SearchProviderError(
                f"invalid or unauthorized {self.name} API key (HTTP {status})"
            )
        if status == 402:
            raise SearchProviderError(f"{self.name} account out of credits (HTTP 402)")
        if status == 429:
            raise SearchProviderError(
                f"{self.name} rate limit or quota reached (HTTP 429)"
            )
        if status >= 400:
            detail = self._error_detail(response)
            raise SearchProviderError(
                f"HTTP {status}: {detail}" if detail else f"HTTP {status}"
            )

    def _error_detail(self, response: httpx.Response) -> str:
        """Vendor error text, scrubbed of the key and capped for tool output."""
        # Error pages can be large HTML; never decode or scan more than this.
        text = response.text[:MAX_ERROR_BODY_CHARS]
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                error = error.get("message")
            for candidate in (error, body.get("message"), body.get("detail")):
                if isinstance(candidate, str) and candidate.strip():
                    text = candidate
                    break
        return truncate_snippet(scrub_key(text, self.secret), MAX_ERROR_DETAIL_CHARS)

    def _json_object(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderError(
                f"invalid JSON response from {self.name}"
            ) from exc
        if not isinstance(data, dict):
            raise self._unexpected()
        return data
