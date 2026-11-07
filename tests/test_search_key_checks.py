"""Checking a search provider key against the provider before it is saved."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from aldwin.core.tools.web_search import (
    BRAVE_SEARCH_URL,
    BraveProvider,
    ExaProvider,
    FirecrawlProvider,
    HostedSearchProvider,
    KeyCheck,
    ParallelProvider,
    PerplexityProvider,
    SearchProviderError,
    TavilyProvider,
)

KEY = "real-looking-key-abcdef123456"
PROVIDERS: list[type[HostedSearchProvider]] = [
    TavilyProvider,
    BraveProvider,
    ExaProvider,
    PerplexityProvider,
    ParallelProvider,
    FirecrawlProvider,
]
Handler = Callable[[httpx.Request], httpx.Response]

EXPECTED_AUTH = {
    TavilyProvider: ("Authorization", f"Bearer {KEY}"),
    BraveProvider: ("X-Subscription-Token", KEY),
    ExaProvider: ("x-api-key", KEY),
    PerplexityProvider: ("Authorization", f"Bearer {KEY}"),
    ParallelProvider: ("x-api-key", KEY),
    FirecrawlProvider: ("Authorization", f"Bearer {KEY}"),
}


async def _check(
    provider_cls: type[HostedSearchProvider], handler: Handler
) -> tuple[KeyCheck, str, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(recording)) as client:
        result = await provider_cls(KEY, client=client).check_key()
    return result.outcome, result.detail, seen


def _status(status: int, body: object = None) -> Handler:
    return lambda request: httpx.Response(status, json=body if body is not None else {})


def _raise(exc_type: type[httpx.HTTPError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type(f"failed for {KEY}", request=request)  # type: ignore[call-arg]

    return handler


@pytest.mark.parametrize("provider_cls", PROVIDERS)
async def test_check_sends_one_get_to_the_check_url_with_auth(
    provider_cls: type[HostedSearchProvider],
) -> None:
    """One GET to the provider's check endpoint, authenticated the provider's way."""
    _, _, seen = await _check(provider_cls, _status(200))

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == provider_cls.check_url
    header, value = EXPECTED_AUTH[provider_cls]
    assert request.headers[header] == value
    assert request.content == b""


@pytest.mark.parametrize(
    ("provider_cls", "status"),
    [
        (TavilyProvider, 200),
        (ExaProvider, 200),
        (PerplexityProvider, 200),
        (FirecrawlProvider, 200),
        (BraveProvider, 200),
        (ParallelProvider, 200),
        (ParallelProvider, 404),
    ],
)
async def test_check_accepts_working_keys(
    provider_cls: type[HostedSearchProvider], status: int
) -> None:
    """A working key is OK; Parallel's made-up run id answers 404 for a good key."""
    outcome, detail, _ = await _check(provider_cls, _status(status))
    assert outcome is KeyCheck.OK
    assert detail == f"HTTP {status}"


@pytest.mark.parametrize("provider_cls", PROVIDERS)
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, KeyCheck.REJECTED),
        (403, KeyCheck.REJECTED),
        (402, KeyCheck.NO_CREDITS),
        (429, KeyCheck.RATE_LIMITED),
        (500, KeyCheck.UNREACHABLE),
        (503, KeyCheck.UNREACHABLE),
        (418, KeyCheck.UNEXPECTED),
    ],
)
async def test_check_maps_statuses(
    provider_cls: type[HostedSearchProvider], status: int, expected: KeyCheck
) -> None:
    """Rejected, out of credits, rate limited and server errors each map once."""
    outcome, detail, _ = await _check(provider_cls, _status(status))
    assert outcome is expected
    assert detail == f"HTTP {status}"


async def test_brave_invalid_token_422_is_rejected() -> None:
    """Brave reports a bad key as 422 SUBSCRIPTION_TOKEN_INVALID."""
    body = {"error": {"code": "SUBSCRIPTION_TOKEN_INVALID", "status": 422}}
    outcome, detail, _ = await _check(BraveProvider, _status(422, body))
    assert outcome is KeyCheck.REJECTED
    assert detail == "HTTP 422"

    other = {"error": {"code": "VALIDATION", "status": 422}}
    outcome, _, _ = await _check(BraveProvider, _status(422, other))
    assert outcome is KeyCheck.UNEXPECTED


@pytest.mark.parametrize("provider_cls", PROVIDERS)
@pytest.mark.parametrize("exc_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_check_network_failures_are_unreachable(
    provider_cls: type[HostedSearchProvider], exc_type: type[httpx.HTTPError]
) -> None:
    """No connection or a timeout means the key was not checked."""
    outcome, detail, _ = await _check(provider_cls, _raise(exc_type))
    assert outcome is KeyCheck.UNREACHABLE
    assert KEY not in detail


async def test_brave_check_is_one_result_search() -> None:
    """Brave has no free check, so it spends exactly one single-result query."""
    _, _, seen = await _check(BraveProvider, _status(200, {"web": {"results": []}}))
    assert len(seen) == 1
    assert str(seen[0].url).split("?")[0] == BRAVE_SEARCH_URL
    assert seen[0].url.params["count"] == "1"
    assert BraveProvider.check_note == "key check uses one Brave query"


@pytest.mark.parametrize(
    "provider_cls", [p for p in PROVIDERS if p not in {BraveProvider, ExaProvider}]
)
async def test_free_checks_never_call_search(
    provider_cls: type[HostedSearchProvider],
) -> None:
    """Checking a key never hits the paid search endpoint."""
    _, _, seen = await _check(provider_cls, _status(200))
    assert all(str(r.url).split("?")[0] != provider_cls.default_endpoint for r in seen)
    assert provider_cls.check_url != provider_cls.default_endpoint
    assert "query" not in (provider_cls.check_note or "")


async def test_brave_search_with_invalid_token_says_unauthorized() -> None:
    """A normal Brave search with a bad key reads as unauthorized, not HTTP 422."""
    body = {"error": {"code": "SUBSCRIPTION_TOKEN_INVALID", "status": 422}}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_status(422, body))
    ) as client:
        with pytest.raises(SearchProviderError) as exc_info:
            await BraveProvider(KEY, client=client).search("q")
    assert str(exc_info.value) == "invalid or unauthorized Brave API key (HTTP 422)"


@pytest.mark.parametrize("provider_cls", PROVIDERS)
async def test_key_only_travels_in_the_auth_header(
    provider_cls: type[HostedSearchProvider],
) -> None:
    """The key never appears in the URL, query string or body of a check."""
    _, _, seen = await _check(provider_cls, _status(200))
    request = seen[0]
    assert KEY not in str(request.url)
    assert KEY.encode() not in request.content
    header, _ = EXPECTED_AUTH[provider_cls]
    others = {k: v for k, v in request.headers.items() if k.lower() != header.lower()}
    assert all(KEY not in v for v in others.values())


@pytest.mark.parametrize("provider_cls", PROVIDERS)
def test_every_provider_has_a_signup_page(
    provider_cls: type[HostedSearchProvider],
) -> None:
    assert provider_cls.signup_url.startswith("https://")


async def test_slow_check_gets_its_own_timeout() -> None:
    """Tavily's free check is slow for a good key, so it waits longer than others."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await TavilyProvider(KEY, client=client).check_key()
        await ExaProvider(KEY, client=client).check_key()
    assert seen[0].extensions["timeout"]["read"] == 30.0
    assert seen[1].extensions["timeout"]["read"] == 10.0
    assert TavilyProvider.check_note == "key check can take up to 20 seconds"


@pytest.mark.parametrize(
    ("provider_cls", "status"),
    [
        (FirecrawlProvider, 404),
        (ParallelProvider, 400),
        (ParallelProvider, 422),
    ],
)
async def test_lookup_endpoints_accept_not_found_for_good_keys(
    provider_cls: type[HostedSearchProvider], status: int
) -> None:
    """Only 401/403 mean a bad key; "not found" or bad input means it was accepted."""
    outcome, _, _ = await _check(provider_cls, _status(status))
    assert outcome is KeyCheck.OK


@pytest.mark.parametrize("list_status", [401, 403])
@pytest.mark.parametrize(
    ("search_status", "expected"),
    [(200, KeyCheck.OK), (401, KeyCheck.REJECTED), (402, KeyCheck.NO_CREDITS)],
)
async def test_exa_rejected_list_falls_back_to_one_search(
    list_status: int, search_status: int, expected: KeyCheck
) -> None:
    """A key refused on the websets list gets one search, which decides."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(list_status, json={"error": "websets not enabled"})
        return httpx.Response(search_status, json={"results": []})

    outcome, _, seen = await _check(ExaProvider, handler)
    assert outcome is expected
    assert ExaProvider.check_note == "key check may use one Exa query"
    assert [r.method for r in seen] == ["GET", "POST"]
    assert seen[1].url.path == "/search"
