"""Unit tests for `SearchProvider`, backends, factory, and `WebSearchTool`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from aldwin.core.home import atomic_write, ensure_home
from aldwin.core.policy import PolicySession, wrap_tools_with_policy
from aldwin.core.settings import (
    AldwinConfig,
    LimitsConfig,
    SearchConfig,
    SecretsConfig,
    load_config,
    save_secrets,
)
from aldwin.core.tools import ToolOutput
from aldwin.core.tools.web_search import (
    DEFAULT_LIMIT,
    MAX_SNIPPET_CHARS,
    NO_KEY_MESSAGE,
    PROVIDER_UNAVAILABLE_MESSAGE,
    SEARXNG_NOT_SET_UP_MESSAGE,
    SearchProvider,
    SearchProviderError,
    SearchResult,
    SearxngProvider,
    TavilyProvider,
    active_search_provider_name,
    build_search_provider,
    build_web_search_tool,
    clear_searxng_detection_cache,
    clear_searxng_pace_state,
    consume_web_search_turn_budget,
    describe_active_search,
    detect_local_searxng,
    reset_web_search_turn_budget,
    resolve_searxng_url,
    searxng_search_endpoint,
    switch_search_provider,
    truncate_snippet,
)
from aldwin.core.tools.web_search import searxng as searxng_mod

# Captured at import time (before the conftest probe stub) for probe unit tests.
_REAL_PROBE = searxng_mod._probe_searxng_json


@pytest.fixture(autouse=True)
def _no_live_searxng_autodetect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests offline: do not probe the developer's local SearXNG."""
    monkeypatch.setattr(searxng_mod, "_LOCAL_SEARXNG_CANDIDATES", ())
    clear_searxng_detection_cache()
    clear_searxng_pace_state()
    reset_web_search_turn_budget()


def _enable_local_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        searxng_mod,
        "_LOCAL_SEARXNG_CANDIDATES",
        ("http://127.0.0.1:8080", "http://localhost:8080"),
    )


class _SpySearchProvider:
    """In-memory `SearchProvider` recording calls and returning canned results."""

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self.results = list(results or [])
        self.calls: list[tuple[str, int]] = []

    async def search(
        self, query: str, limit: int = DEFAULT_LIMIT
    ) -> list[SearchResult]:
        self.calls.append((query, limit))
        return self.results[:limit]


def test_truncate_snippet_caps_at_max_chars() -> None:
    """Collapse whitespace and append ``...`` past the cap."""
    assert truncate_snippet("short") == "short"
    assert truncate_snippet("  a   b  ") == "a b"
    long = "x" * (MAX_SNIPPET_CHARS + 40)
    trimmed = truncate_snippet(long)
    assert trimmed.endswith("...")
    assert len(trimmed) == MAX_SNIPPET_CHARS + len("...")


async def test_web_search_truncates_long_snippets(aldwin_home: Path) -> None:
    """Tool output snippets stay within MAX_SNIPPET_CHARS for context hygiene."""
    ensure_home(aldwin_home)
    long_body = ("word " * 80).strip()
    spy = _SpySearchProvider(
        [
            SearchResult(
                title="Long Hit",
                url="https://example.com/long",
                snippet=long_body,
            )
        ]
    )
    tool = build_web_search_tool(home=aldwin_home, provider=spy)
    res = await tool.call({"query": "context trim", "limit": 1})
    assert res.ok is True
    text = str(res)
    snippet_line = text.split("\n", 1)[1].strip()
    assert snippet_line.endswith("...")
    assert len(snippet_line) == MAX_SNIPPET_CHARS + len("...")


async def test_web_search_formats_results_and_summary(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-01: `web_search` with mocked Tavily returns numbered list and `3 results`."""
    ensure_home(aldwin_home)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-12345678")

    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "What's New In Python 3.14",
                        "url": "https://docs.python.org/3.14/whatsnew/3.14.html",
                        "content": "Summary of release\nhighlights and PEP 649.",
                    },
                    {
                        "title": "PEP 745 - Python 3.14 Release Schedule",
                        "url": "https://peps.python.org/pep-0745/",
                        "content": "Release calendar and feature freeze dates.",
                    },
                    {
                        "title": "Python 3.14 Performance Guide",
                        "url": "https://realpython.com/python314/",
                        "content": "Tail-call interpreter and free-threading updates.",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = TavilyProvider(
            "tvly-test-key-12345678",
            client=http_client,
        )
        tool = build_web_search_tool(home=aldwin_home, provider=provider)
        res = await tool.call({"query": "Python 3.14 release notes", "limit": 3})

    assert isinstance(res, ToolOutput)
    assert res.ok is True
    assert res.summary == "3 results"

    expected = (
        "1. What's New In Python 3.14 - https://docs.python.org/3.14/whatsnew/3.14.html\n"
        "   Summary of release highlights and PEP 649.\n"
        "2. PEP 745 - Python 3.14 Release Schedule - https://peps.python.org/pep-0745/\n"
        "   Release calendar and feature freeze dates.\n"
        "3. Python 3.14 Performance Guide - https://realpython.com/python314/\n"
        "   Tail-call interpreter and free-threading updates."
    )
    assert str(res) == expected

    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://api.tavily.com/search"
    assert req.headers["authorization"] == "Bearer tvly-test-key-12345678"
    body = json.loads(req.content.decode("utf-8"))
    assert body == {
        "query": "Python 3.14 release notes",
        "max_results": 3,
        "search_depth": "basic",
    }


async def test_web_search_without_key_returns_setup_message(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-02: `web_search` with no Tavily key returns setup message without raising."""
    ensure_home(aldwin_home)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    tool = build_web_search_tool(home=aldwin_home)
    res = await tool.call({"query": "latest Python release"})

    assert isinstance(res, ToolOutput)
    assert str(res) == NO_KEY_MESSAGE
    assert res.summary == "not set up"
    assert res.ok is False


async def test_web_search_query_and_limit_boundaries(aldwin_home: Path) -> None:
    """TC-15: `web_search` validates query (1..400 chars) and `limit` (1..10) bounds."""
    ensure_home(aldwin_home)
    spy = _SpySearchProvider(
        [
            SearchResult(
                title=f"Item {i}",
                url=f"https://example.com/{i}",
                snippet=f"Snippet {i}",
            )
            for i in range(1, 12)
        ]
    )
    tool = build_web_search_tool(home=aldwin_home, provider=spy)

    for bad_query in ("", "   \n\t  ", "bad\x00query", "x" * 401):
        out = await tool.call({"query": bad_query})
        assert isinstance(out, ToolOutput)
        assert out.ok is False
        assert out.summary == "invalid query"
        assert "Invalid search query" in str(out)

    for bad_limit in (0, -1, 11):
        out = await tool.call({"query": "valid", "limit": bad_limit})
        assert isinstance(out, ToolOutput)
        assert out.ok is False
        assert out.summary == "invalid limit"
        assert "Invalid search limit" in str(out)

    # Direct forward() check with bool limit.
    bool_out = await tool.forward("valid", True)
    assert bool_out.ok is False
    assert bool_out.summary == "invalid limit"

    assert spy.calls == []

    # Valid boundary: 400-char query, limit=1, limit=10.
    q_400 = "q" * 400
    res_1 = await tool.call({"query": q_400, "limit": 1})
    assert res_1.ok is True
    assert res_1.summary == "1 result"

    res_10 = await tool.call({"query": "ten results", "limit": 10})
    assert res_10.ok is True
    assert res_10.summary == "10 results"
    assert spy.calls == [(q_400, 1), ("ten results", 10)]


async def test_tavily_provider_error_and_timeout_handling(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-16: Errors and timeouts return `search failed: ...` without leaking keys."""
    ensure_home(aldwin_home)
    secret_key = "tvly-supersecret99998888"
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    save_secrets(
        SecretsConfig(tavily_api_key=SecretStr(secret_key)),
        aldwin_home,
    )

    with pytest.raises(ValueError, match="must not be empty"):
        TavilyProvider("   ")

    cases: list[tuple[Any, str]] = [
        (lambda req: httpx.Response(401, text="Unauthorized"), "HTTP 401"),
        (lambda req: httpx.Response(429, text="Rate limit"), "HTTP 429"),
        (lambda req: httpx.Response(503, text="Down"), "HTTP 503"),
        (lambda req: httpx.Response(200, content=b"not-json{"), "invalid JSON"),
        (
            lambda req: httpx.Response(200, json={"unexpected": 123}),
            "unexpected response",
        ),
    ]

    for handler, expected_fragment in cases:
        reset_web_search_turn_budget()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http_client:
            provider = TavilyProvider(secret_key, client=http_client)
            tool = build_web_search_tool(home=aldwin_home, provider=provider)
            res = await tool.call({"query": "test error"})
            assert res.ok is False
            assert str(res).startswith("search failed: ")
            assert expected_fragment in str(res)
            assert secret_key not in str(res)
            assert secret_key not in res.summary

    # Transport raising ConnectError containing the secret key in the message.
    def raising_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Failed with token {secret_key}", request=request)

    reset_web_search_turn_budget()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(raising_handler)
    ) as http_client:
        provider = TavilyProvider(secret_key, client=http_client)
        tool = build_web_search_tool(home=aldwin_home, provider=provider)
        res_conn = await tool.call({"query": "connect error"})
        assert res_conn.ok is False
        assert secret_key not in str(res_conn)
        assert "[redacted]" in str(res_conn)

    # Timeout in provider / tool wrapper.
    class _SlowProvider:
        async def search(
            self, query: str, limit: int = DEFAULT_LIMIT
        ) -> list[SearchResult]:
            del query, limit
            await asyncio.sleep(5.0)
            return []

    reset_web_search_turn_budget()
    slow_tool = build_web_search_tool(
        home=aldwin_home,
        provider=_SlowProvider(),
        timeout=0.05,
    )
    res_timeout = await slow_tool.call({"query": "slow query"})
    assert res_timeout.ok is False
    assert "timed out" in str(res_timeout)
    assert res_timeout.summary == "search failed: timed out"

    # httpx.TimeoutException inside TavilyProvider.
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler)
    ) as http_client:
        provider_to = TavilyProvider(secret_key, client=http_client)
        with pytest.raises(SearchProviderError, match="request timed out"):
            await provider_to.search("query")


async def test_web_search_zero_and_single_result_and_custom_provider(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-17: `web_search` handles 0 results, 1 result, and custom `SearchProvider`."""
    ensure_home(aldwin_home)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    empty_provider = _SpySearchProvider([])
    assert isinstance(empty_provider, SearchProvider)

    tool_empty = build_web_search_tool(home=aldwin_home, provider=empty_provider)
    res_0 = await tool_empty.call({"query": "nothing matches"})
    assert str(res_0) == "No results found."
    assert res_0.summary == "0 results"
    assert res_0.ok is True

    single_provider = _SpySearchProvider(
        [
            SearchResult(
                title="   ",
                url="https://agentling.dev",
                snippet="Lightweight Python agent framework.",
            )
        ]
    )
    tool_single = build_web_search_tool(home=aldwin_home, provider=single_provider)
    res_1 = await tool_single.call({"query": "agentling"})
    assert str(res_1) == (
        "1. https://agentling.dev - https://agentling.dev\n"
        "   Lightweight Python agent framework."
    )
    assert res_1.summary == "1 result"
    assert res_1.ok is True

    # Also test key resolution from SecretsConfig when passed directly.
    secrets = SecretsConfig(tavily_api_key=SecretStr("tvly-from-secrets-obj"))

    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tvly-from-secrets-obj"
        return httpx.Response(
            200,
            json={
                "results": [
                    "not-a-dict",
                    {"title": "No URL", "url": ""},
                    {"title": "Valid", "url": "https://example.com", "snippet": "Text"},
                ]
            },
        )

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(ok_handler),
            timeout=kwargs.get("timeout"),
        ),
    )
    tool_from_secrets = build_web_search_tool(home=aldwin_home, secrets=secrets)
    res_sec = await tool_from_secrets.call({"query": "test secrets"})
    assert res_sec.ok is True
    assert res_sec.summary == "1 result"
    assert "1. Valid - https://example.com\n   Text" == str(res_sec)


def test_searxng_search_endpoint_bare_and_doubled() -> None:
    """Join /search once whether or not the base already ends with /search."""
    assert searxng_search_endpoint("http://localhost:8080") == (
        "http://localhost:8080/search"
    )
    assert searxng_search_endpoint("http://localhost:8080/") == (
        "http://localhost:8080/search"
    )
    assert searxng_search_endpoint("http://localhost:8080/search") == (
        "http://localhost:8080/search"
    )
    assert searxng_search_endpoint("http://localhost:8080/search/") == (
        "http://localhost:8080/search"
    )


def test_resolve_searxng_url_config_and_env(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Config URL wins over env; SEARXNG_URL wins over SEARXNG_BASE_URL."""
    monkeypatch.setenv("SEARXNG_URL", "http://env-url:8080")
    monkeypatch.setenv("SEARXNG_BASE_URL", "http://base-url:8080")
    cfg = AldwinConfig(
        search=SearchConfig(searxng_url="http://config-url:8080"),
    )
    assert resolve_searxng_url(config=cfg) == "http://config-url:8080"

    monkeypatch.delenv("SEARXNG_URL", raising=False)
    assert resolve_searxng_url(config=AldwinConfig()) == "http://base-url:8080"

    monkeypatch.setenv("SEARXNG_URL", "http://env-url:8080")
    assert resolve_searxng_url(config=AldwinConfig()) == "http://env-url:8080"

    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    assert resolve_searxng_url(home=aldwin_home) is None


def test_detect_local_searxng_when_json_responds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unconfigured installs pick up a local JSON-enabled SearXNG on :8080."""

    def probe(base_url: str, *, timeout: float = 0.35) -> bool:
        _ = timeout
        return base_url.rstrip("/") == "http://127.0.0.1:8080"

    _enable_local_candidates(monkeypatch)
    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", probe)
    clear_searxng_detection_cache()
    assert detect_local_searxng(force=True) == "http://127.0.0.1:8080"
    assert resolve_searxng_url(config=AldwinConfig()) == "http://127.0.0.1:8080"
    assert active_search_provider_name(config=AldwinConfig()) == "SearXNG"


async def test_searxng_provider_happy_path_and_limit() -> None:
    """Map scored SearXNG results and clamp to the requested limit."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        hits = [
            {
                "title": f"Hit {i}",
                "url": f"https://example.com/{i}",
                "content": f"Snippet {i}",
                "score": float(i),
            }
            for i in range(1, 21)
        ]
        return httpx.Response(200, json={"results": hits})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SearxngProvider("http://127.0.0.1:8080", client=client)
        top2 = await provider.search("q", limit=2)
        assert len(top2) == 2
        assert top2[0].title == "Hit 20"
        assert top2[0].url == "https://example.com/20"
        assert top2[0].snippet == "Snippet 20"
        assert top2[1].title == "Hit 19"

        top10 = await provider.search("q", limit=10)
        assert len(top10) == 10

    assert captured
    first = captured[0]
    assert first.method == "GET"
    parsed = urlparse(str(first.url))
    assert parsed.scheme == "http"
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == 8080
    assert parsed.path == "/search"
    params = parse_qs(parsed.query)
    assert params["q"] == ["q"]
    assert params["format"] == ["json"]
    assert params["pageno"] == ["1"]


async def test_searxng_provider_error_mapping() -> None:
    """Map 403 JSON-off, 429, timeout, and bad JSON or shape to SearchProviderError."""
    cases: list[tuple[Any, str]] = [
        (
            lambda req: httpx.Response(403, text="Forbidden"),
            "format=json",
        ),
        (
            lambda req: httpx.Response(429, text="Slow down"),
            "429",
        ),
        (
            lambda req: httpx.Response(200, content=b"not-json{"),
            "invalid JSON",
        ),
        (
            lambda req: httpx.Response(200, json={"query": "x"}),
            "unexpected response",
        ),
    ]
    for handler, fragment in cases:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = SearxngProvider("http://127.0.0.1:8080", client=client)
            with pytest.raises(SearchProviderError, match=fragment):
                await provider.search("q")

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler)
    ) as client:
        provider = SearxngProvider("http://127.0.0.1:8080", client=client)
        with pytest.raises(SearchProviderError, match="request timed out"):
            await provider.search("q")


def test_build_search_provider_auto_matrix(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto picks Tavily over SearXNG, and None when nothing is configured."""
    ensure_home(aldwin_home)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)

    assert build_search_provider(home=aldwin_home) is None

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-12345678")
    only_tavily = build_search_provider(home=aldwin_home)
    assert isinstance(only_tavily, TavilyProvider)

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8080")
    only_searxng = build_search_provider(home=aldwin_home)
    assert isinstance(only_searxng, SearxngProvider)

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-12345678")
    both = build_search_provider(home=aldwin_home)
    assert isinstance(both, TavilyProvider)


def test_active_search_provider_name(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Welcome-line helper returns the resolved backend display name."""
    ensure_home(aldwin_home)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)

    assert active_search_provider_name(home=aldwin_home) is None

    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8080")
    assert active_search_provider_name(home=aldwin_home) == "SearXNG"

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-12345678")
    assert active_search_provider_name(home=aldwin_home) == "Tavily"


def test_switch_search_provider_persists(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/search` helper persists provider and refuses unready backends."""
    ensure_home(aldwin_home)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)

    refused = switch_search_provider("searxng", home=aldwin_home)
    assert refused.ok is False
    assert "isn't ready" in refused.message
    assert load_config(aldwin_home).search.provider == "auto"

    unknown = switch_search_provider("nope", home=aldwin_home)
    assert unknown.ok is False
    assert "Unknown" in unknown.message

    unavailable = switch_search_provider("brave", home=aldwin_home)
    assert unavailable.ok is False
    assert "not available" in unavailable.message

    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-12345678")

    to_searx = switch_search_provider("searx", home=aldwin_home)
    assert to_searx.ok is True
    assert to_searx.message == "Search · SearXNG"
    assert load_config(aldwin_home).search.provider == "searxng"
    assert active_search_provider_name(home=aldwin_home) == "SearXNG"

    to_auto = switch_search_provider("auto", home=aldwin_home)
    assert to_auto.ok is True
    assert "(auto)" in to_auto.message
    assert load_config(aldwin_home).search.provider == "auto"
    assert active_search_provider_name(home=aldwin_home) == "Tavily"

    status = describe_active_search(home=aldwin_home)
    assert "Search · Tavily (auto)" in status
    assert "Providers:" in status
    assert "tavily" in status and "ready" in status
    assert "searxng" in status


async def test_web_search_factory_wiring(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory messages, SearXNG success on localhost, and injected provider win."""
    ensure_home(aldwin_home)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)

    bare = build_web_search_tool(home=aldwin_home)
    res_bare = await bare.call({"query": "news"})
    assert res_bare.ok is False
    assert str(res_bare) == NO_KEY_MESSAGE
    assert "searxng_url" in NO_KEY_MESSAGE
    assert "API key" in NO_KEY_MESSAGE
    assert "Tavily" not in NO_KEY_MESSAGE

    atomic_write(
        aldwin_home / "config.yaml",
        "model: gpt-4o\nsearch:\n  provider: searxng\n",
    )
    searxng_missing = build_web_search_tool(home=aldwin_home)
    res_sx = await searxng_missing.call({"query": "news"})
    assert res_sx.ok is False
    assert str(res_sx) == SEARXNG_NOT_SET_UP_MESSAGE
    assert res_sx.summary == "not set up"
    assert "SearXNG" in str(res_sx) or "searxng_url" in str(res_sx)

    atomic_write(
        aldwin_home / "config.yaml",
        "model: gpt-4o\nsearch:\n  provider: brave\n",
    )
    brave_tool = build_web_search_tool(home=aldwin_home)
    res_brave = await brave_tool.call({"query": "news"})
    assert res_brave.ok is False
    assert str(res_brave) == PROVIDER_UNAVAILABLE_MESSAGE.format(name="brave")
    assert res_brave.summary == "not available"

    # Localhost SearXNG is allowed; this path never goes through web_fetch SSRF guards.
    atomic_write(
        aldwin_home / "config.yaml",
        (
            "model: gpt-4o\n"
            "search:\n"
            "  provider: auto\n"
            "  searxng_url: http://127.0.0.1:8080\n"
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "127.0.0.1" in str(request.url)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Local Result",
                        "url": "https://example.com/local",
                        "content": "From SearXNG",
                        "score": 1.0,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = build_web_search_tool(home=aldwin_home, http_client=client)
        res_ok = await tool.call({"query": "local news", "limit": 1})
    assert res_ok.ok is True
    assert res_ok.summary == "1 result"
    assert "1. Local Result - https://example.com/local\n   From SearXNG" == str(res_ok)

    atomic_write(aldwin_home / "config.yaml", "model: gpt-4o\n")
    spy = _SpySearchProvider(
        [SearchResult(title="Spy", url="https://example.com/spy", snippet="Injected")]
    )
    tool_spy = build_web_search_tool(home=aldwin_home, provider=spy)
    res_spy = await tool_spy.call({"query": "anything"})
    assert res_spy.ok is True
    assert spy.calls == [("anything", DEFAULT_LIMIT)]


async def test_searxng_paces_consecutive_searches() -> None:
    """Local SearXNG serializes calls and waits at least min_interval."""
    import time

    clear_searxng_pace_state()
    hits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(time.monotonic())
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Hit",
                        "url": "https://example.com/a",
                        "content": "ok",
                        "score": 1.0,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SearxngProvider(
            "http://127.0.0.1:8080",
            client=client,
            min_interval=0.05,
        )
        await provider.search("one", limit=1)
        await provider.search("two", limit=1)

    assert len(hits) == 2
    assert hits[1] - hits[0] >= 0.045


async def test_searxng_surfaces_upstream_rate_limits() -> None:
    """Empty results plus suspended engines become a clear SearchProviderError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [],
                "unresponsive_engines": [
                    ["brave", "Suspended: too many requests"],
                    ["google", "CAPTCHA"],
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SearxngProvider(
            "http://127.0.0.1:8080",
            client=client,
            min_interval=0.0,
        )
        with pytest.raises(SearchProviderError, match="rate-limited or blocked"):
            await provider.search("Folarin", limit=5)


async def test_web_search_turn_budget_caps_calls(aldwin_home: Path) -> None:
    """limits.web_searches stops further web_search calls in the same turn."""
    ensure_home(aldwin_home)
    spy = _SpySearchProvider(
        [SearchResult(title="A", url="https://example.com/a", snippet="s")]
    )
    cfg = AldwinConfig(limits=LimitsConfig(web_searches=2))
    tool = build_web_search_tool(home=aldwin_home, provider=spy, config=cfg)

    first = await tool.call({"query": "one"})
    second = await tool.call({"query": "two"})
    third = await tool.call({"query": "three"})

    assert first.ok is True and second.ok is True
    assert third.ok is False
    assert third.summary == "search limit reached"
    assert "limit reached for this turn (2)" in str(third)
    assert spy.calls == [("one", DEFAULT_LIMIT), ("two", DEFAULT_LIMIT)]

    reset_web_search_turn_budget()
    fourth = await tool.call({"query": "four"})
    assert fourth.ok is True
    assert spy.calls[-1] == ("four", DEFAULT_LIMIT)


def test_web_search_parallel_safe_false_for_local_only(aldwin_home: Path) -> None:
    """SearXNG opts out of parallel tool steps; Tavily stays parallel_safe."""
    ensure_home(aldwin_home)
    searx = SearxngProvider("http://127.0.0.1:8080", min_interval=0.0)
    tavily = TavilyProvider("tvly-test-key-12345678")

    local_tool = build_web_search_tool(home=aldwin_home, provider=searx)
    hosted_tool = build_web_search_tool(home=aldwin_home, provider=tavily)
    assert local_tool.parallel_safe is False
    assert hosted_tool.parallel_safe is True

    async def _ask(_request: Any) -> Any:
        return "yes"

    session = PolicySession(home=aldwin_home)
    wrapped_local = wrap_tools_with_policy(
        [local_tool],
        session=session,
        ask=_ask,
    )[0]
    wrapped_hosted = wrap_tools_with_policy(
        [hosted_tool],
        session=session,
        ask=_ask,
    )[0]
    assert wrapped_local.parallel_safe is False
    assert wrapped_hosted.parallel_safe is True


async def test_web_search_turn_budget_survives_gather() -> None:
    """Child tasks from gather share one mutable turn budget."""
    reset_web_search_turn_budget()

    async def once() -> str | None:
        return consume_web_search_turn_budget(2)

    results = await asyncio.gather(once(), once(), once())
    assert results.count(None) == 2
    assert sum(1 for item in results if item is not None) == 1


async def test_web_search_turn_budget_via_host_dispatch(
    aldwin_home: Path,
    fake_model: Any,
) -> None:
    """limits.web_searches is enforced when Agentling dispatches tools."""
    from agentling import Agent
    from fakes.model import FakeModel, FakeTurn

    from aldwin.client import AldwinClient
    from aldwin.host import AldwinHost, InProcessTransport
    from aldwin.protocol import ToolResultEvent

    assert isinstance(fake_model, FakeModel)
    ensure_home(aldwin_home)
    spy = _SpySearchProvider(
        [SearchResult(title="A", url="https://example.com/a", snippet="s")]
    )
    cfg = AldwinConfig(limits=LimitsConfig(web_searches=2))
    tool = build_web_search_tool(home=aldwin_home, provider=spy, config=cfg)

    fake_model.add_turns(
        FakeTurn(
            tool_calls=[
                ("c1", "web_search", {"query": "one"}),
                ("c2", "web_search", {"query": "two"}),
                ("c3", "web_search", {"query": "three"}),
            ]
        ),
        FakeTurn.final("done"),
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model, tools=[tool]),
    )
    await host.start()
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        events: list[Any] = []
        async for ev in client.turn("search thrice"):
            events.append(ev)
        results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
        assert len(results) == 3
        assert sum(1 for ev in results if ev.ok) == 2
        assert sum(1 for ev in results if not ev.ok) == 1
        assert any("limit reached" in (ev.summary or "") for ev in results if not ev.ok)
        assert len(spy.calls) == 2
    finally:
        await client.close()
        await host.stop_host()


async def test_web_search_forward_uses_detected_searxng(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no URL is configured, forward still uses a detected local SearXNG."""
    ensure_home(aldwin_home)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)

    def probe(base_url: str, *, timeout: float = 0.35) -> bool:
        _ = timeout
        return base_url.rstrip("/") == "http://127.0.0.1:8080"

    _enable_local_candidates(monkeypatch)
    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", probe)
    clear_searxng_detection_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Detected",
                        "url": "https://example.com/detected",
                        "content": "from local",
                        "score": 1.0,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = build_web_search_tool(
            home=aldwin_home,
            config=AldwinConfig(),
            http_client=client,
        )
        res = await tool.call({"query": "hello"})

    assert res.ok is True
    assert "Detected" in str(res)
    assert "https://example.com/detected" in str(res)


def test_probe_rejects_non_searxng_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A generic :8080 ``/config`` without SearXNG keys must not match."""

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = args, kwargs

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: Any) -> None:
            _ = args

        def get(self, url: str) -> httpx.Response:
            if url.rstrip("/").endswith("/config"):
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(200, text="OK")

    _enable_local_candidates(monkeypatch)
    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", _REAL_PROBE)
    monkeypatch.setattr(httpx, "Client", _Client)
    clear_searxng_detection_cache()
    assert searxng_mod._probe_searxng_json("http://127.0.0.1:8080") is False
    assert detect_local_searxng(force=True) is None


def test_probe_accepts_searxng_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/config`` with SearXNG keys is enough for local detection."""

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = args, kwargs

        def __enter__(self) -> _Client:
            return self

        def __exit__(self, *args: Any) -> None:
            _ = args

        def get(self, url: str) -> httpx.Response:
            assert url.rstrip("/").endswith("/config")
            return httpx.Response(
                200,
                json={"engines": [], "instance_name": "searxng"},
            )

    _enable_local_candidates(monkeypatch)
    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", _REAL_PROBE)
    monkeypatch.setattr(httpx, "Client", _Client)
    clear_searxng_detection_cache()
    assert searxng_mod._probe_searxng_json("http://127.0.0.1:8080") is True
    assert detect_local_searxng(force=True) == "http://127.0.0.1:8080"
