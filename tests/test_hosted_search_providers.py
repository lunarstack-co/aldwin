"""Hosted search providers: request shapes, mapping, errors, keys, and factory."""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

import aldwin
from aldwin.core.home import atomic_write
from aldwin.core.settings import SecretsConfig, save_secrets
from aldwin.core.tools import web_search
from aldwin.core.tools.web_search import (
    BRAVE_SEARCH_URL,
    EXA_SEARCH_URL,
    FIRECRAWL_SEARCH_URL,
    PARALLEL_SEARCH_URL,
    PERPLEXITY_SEARCH_URL,
    TAVILY_SEARCH_URL,
    BraveProvider,
    ExaProvider,
    FirecrawlProvider,
    HostedSearchProvider,
    ParallelProvider,
    PerplexityProvider,
    SearchProvider,
    SearchProviderError,
    SearchResult,
    SearxngProvider,
    TavilyProvider,
    build_search_provider,
    build_web_search_tool,
    describe_active_search,
    ready_search_provider_names,
    reset_web_search_turn_budget,
    resolve_search_api_key,
    resolve_tavily_key,
    switch_search_provider,
)
from aldwin.core.tools.web_search import hosted as hosted_mod

KEY = "test-key-abcdef123456"

Handler = Callable[[httpx.Request], httpx.Response]

HOSTED_PROVIDERS: list[tuple[type[HostedSearchProvider], str]] = [
    (TavilyProvider, "Tavily"),
    (BraveProvider, "Brave"),
    (ExaProvider, "Exa"),
    (PerplexityProvider, "Perplexity"),
    (ParallelProvider, "Parallel"),
    (FirecrawlProvider, "Firecrawl"),
]
HOSTED_CLASSES = [cls for cls, _ in HOSTED_PROVIDERS]


def _wrap_hits(cls: type[HostedSearchProvider], hits: list[dict[str, Any]]) -> Any:
    """Build each vendor's success body around generic title/url/snippet hits."""
    if cls is BraveProvider:
        return {"web": {"results": [_with(h, "description") for h in hits]}}
    if cls is FirecrawlProvider:
        return {
            "success": True,
            "data": {"web": [_with(h, "description") for h in hits]},
        }
    if cls is ExaProvider:
        return {"results": [{**_strip(h), "highlights": [h["snippet"]]} for h in hits]}
    if cls is ParallelProvider:
        return {"results": [{**_strip(h), "excerpts": [h["snippet"]]} for h in hits]}
    if cls is TavilyProvider:
        return {"results": [_with(h, "content") for h in hits]}
    return {"results": hits}


def _strip(hit: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in hit.items() if k != "snippet"}


def _with(hit: dict[str, Any], field: str) -> dict[str, Any]:
    return {**_strip(hit), field: hit["snippet"]}


def _hits(count: int) -> list[dict[str, Any]]:
    return [
        {"title": f"T{i}", "url": f"https://example.com/{i}", "snippet": f"S{i}"}
        for i in range(count)
    ]


async def _run(
    provider_cls: type[HostedSearchProvider],
    handler: Handler,
    query: str = "python",
    limit: int = 5,
) -> list[SearchResult]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await provider_cls(KEY, client=client).search(query, limit)


def _capture(body: Any, status: int = 200) -> tuple[list[httpx.Request], Handler]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return seen, handler


@pytest.fixture(autouse=True)
def _clear_search_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SEARXNG_URL", "SEARXNG_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    reset_web_search_turn_budget()


# Providers


async def test_brave_request_and_mapping() -> None:
    """GET with X-Subscription-Token; maps web.results description."""
    seen, handler = _capture(_wrap_hits(BraveProvider, _hits(3)))
    results = await _run(BraveProvider, handler, limit=3)

    request = seen[0]
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == BRAVE_SEARCH_URL
    assert dict(request.url.params) == {"q": "python", "count": "3"}
    assert request.headers["X-Subscription-Token"] == KEY
    assert request.headers["Accept"] == "application/json"
    assert "Authorization" not in request.headers
    assert results == [
        SearchResult(title=f"T{i}", url=f"https://example.com/{i}", snippet=f"S{i}")
        for i in range(3)
    ]


async def test_brave_no_web_key_is_empty(aldwin_home: Path) -> None:
    """Brave omits `web` on no hits; tool says No results found."""
    _, handler = _capture({"query": {"original": "zzz"}})
    assert await _run(BraveProvider, handler) == []

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = build_web_search_tool(
            home=aldwin_home, provider=BraveProvider(KEY, client=client)
        )
        res = await tool.call({"query": "zzz"})
    assert res.ok is True
    assert str(res) == "No results found."
    assert res.summary == "0 results"

    _, bad = _capture({"web": "not an object"})
    with pytest.raises(SearchProviderError, match="unexpected response format"):
        await _run(BraveProvider, bad)


def test_brave_key_alias_and_primary_wins(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BRAVE_SEARCH_API_KEY works; BRAVE_API_KEY wins when both set."""
    atomic_write(aldwin_home / "config.yaml", "search:\n  provider: brave\n")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "alias-key")
    alias = build_search_provider(home=aldwin_home)
    assert isinstance(alias, BraveProvider)
    assert alias.secret == "alias-key"

    monkeypatch.setenv("BRAVE_API_KEY", "primary-key")
    primary = build_search_provider(home=aldwin_home)
    assert isinstance(primary, BraveProvider)
    assert primary.secret == "primary-key"


async def test_exa_request_and_mapping() -> None:
    """x-api-key POST with highlights; snippet joins highlights, else text."""
    body = {
        "results": [
            {"title": "A", "url": "https://a.example", "highlights": ["one", "two"]},
            {"title": "B", "url": "https://b.example", "text": "full text"},
        ]
    }
    seen, handler = _capture(body)
    results = await _run(ExaProvider, handler, query="q", limit=2)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == EXA_SEARCH_URL
    assert request.headers["x-api-key"] == KEY
    assert "Authorization" not in request.headers
    assert json.loads(request.content) == {
        "query": "q",
        "numResults": 2,
        "type": "auto",
        "contents": {"highlights": True},
    }
    assert [r.snippet for r in results] == ["one two", "full text"]


@pytest.mark.parametrize(
    ("provider_cls", "name"), [(ExaProvider, "Exa"), (FirecrawlProvider, "Firecrawl")]
)
async def test_payment_required_message(
    provider_cls: type[HostedSearchProvider], name: str
) -> None:
    """HTTP 402 means the account is out of credits."""
    _, handler = _capture({"error": "Payment required"}, status=402)
    with pytest.raises(SearchProviderError) as exc_info:
        await _run(provider_cls, handler)
    assert str(exc_info.value) == f"{name} account out of credits (HTTP 402)"


async def test_perplexity_request_and_mapping() -> None:
    """POST /search with max_results; never chat completions."""
    seen, handler = _capture(_wrap_hits(PerplexityProvider, _hits(4)))
    results = await _run(PerplexityProvider, handler, query="q", limit=4)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == PERPLEXITY_SEARCH_URL
    assert "/chat/completions" not in request.url.path
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {"query": "q", "max_results": 4}
    assert [r.snippet for r in results] == ["S0", "S1", "S2", "S3"]


async def test_parallel_request_and_mapping() -> None:
    """x-api-key only; limits nested under advanced_settings."""
    body = {
        "search_id": "s1",
        "results": [
            {"title": "A", "url": "https://a.example", "excerpts": ["first", "second"]},
            {"title": "B", "url": "https://b.example", "excerpts": []},
        ],
    }
    seen, handler = _capture(body)
    results = await _run(ParallelProvider, handler, query="q", limit=5)

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == PARALLEL_SEARCH_URL
    assert request.headers["x-api-key"] == KEY
    assert "Authorization" not in request.headers
    sent = json.loads(request.content)
    assert sent == {
        "search_queries": ["q"],
        "advanced_settings": {
            "max_results": 5,
            "excerpt_settings": {"max_chars_per_result": 600},
        },
    }
    assert "max_results" not in sent
    assert [r.snippet for r in results] == ["first", ""]


@pytest.mark.parametrize("length", [200, 201, 400])
async def test_parallel_truncates_query_to_200(length: int) -> None:
    """Parallel caps each query entry at 200 chars; others send it whole."""
    query = "x" * length
    seen, handler = _capture({"results": []})
    await _run(ParallelProvider, handler, query=query)
    assert json.loads(seen[0].content)["search_queries"] == [query[:200]]

    seen_exa, handler_exa = _capture({"results": []})
    await _run(ExaProvider, handler_exa, query=query)
    assert json.loads(seen_exa[0].content)["query"] == query


async def test_parallel_422_surfaces_message() -> None:
    """Vendor error message is shown, capped at 300 chars, key scrubbed."""
    message = f"search_queries invalid for {KEY} " + "y" * 400
    _, handler = _capture(
        {"type": "error", "error": {"ref_id": "r1", "message": message}}, status=422
    )
    with pytest.raises(SearchProviderError) as exc_info:
        await _run(ParallelProvider, handler)
    text = str(exc_info.value)
    assert text.startswith("HTTP 422: search_queries invalid for [redacted] y")
    assert text.endswith("...")
    assert len(text) == len("HTTP 422: ") + 300 + 3
    assert KEY not in text


@pytest.mark.parametrize(
    "body",
    [
        {
            "success": True,
            "data": {
                "web": [{"title": "A", "url": "https://a.example", "description": "d"}]
            },
        },
        {
            "success": True,
            "data": [{"title": "A", "url": "https://a.example", "description": "d"}],
        },
    ],
    ids=["v2-grouped", "flat"],
)
async def test_firecrawl_request_and_mapping(body: dict[str, Any]) -> None:
    """POST /v2/search with limit; both response shapes map."""
    seen, handler = _capture(body)
    results = await _run(FirecrawlProvider, handler, query="q", limit=3)

    request = seen[0]
    assert str(request.url) == FIRECRAWL_SEARCH_URL
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {"query": "q", "limit": 3}
    assert results == [SearchResult(title="A", url="https://a.example", snippet="d")]


async def test_firecrawl_success_false_and_no_scrape() -> None:
    """success false raises; only /v2/search is ever requested."""
    seen, handler = _capture({"success": False, "error": "Bad plan"})
    with pytest.raises(SearchProviderError) as exc_info:
        await _run(FirecrawlProvider, handler)
    assert str(exc_info.value) == "Firecrawl search failed: Bad plan"

    ok_seen, ok_handler = _capture({"success": True, "data": {"web": []}})
    assert await _run(FirecrawlProvider, ok_handler) == []
    assert {r.url.path for r in seen + ok_seen} == {"/v2/search"}


def test_tavily_is_hosted_provider(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tavily runs on the shared base and keeps its endpoint and key helper."""
    assert issubclass(TavilyProvider, HostedSearchProvider)
    provider = TavilyProvider(KEY)
    assert isinstance(provider, SearchProvider)
    assert provider._endpoint == TAVILY_SEARCH_URL

    assert resolve_tavily_key(aldwin_home, None) is None
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env")
    assert resolve_tavily_key(aldwin_home, None) == "tvly-env"


def test_unavailable_provider_message_is_gone() -> None:
    """Every catalogue name is implemented, so the old placeholder is not exported."""
    assert "PROVIDER_UNAVAILABLE_MESSAGE" not in web_search.__all__


# Shared contract, parametrised over every hosted provider


def _status(status: int, body: Any = None, text: str | None = None) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=body)

    return handler


def _raises(exc_type: type[httpx.HTTPError], message: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type(message, request=request)  # type: ignore[call-arg]

    return handler


ERROR_CASES: list[tuple[str, Handler, str]] = [
    (
        "401",
        _status(401, {"error": "nope"}),
        "invalid or unauthorized {name} API key (HTTP 401)",
    ),
    (
        "403",
        _status(403, {"error": "nope"}),
        "invalid or unauthorized {name} API key (HTTP 403)",
    ),
    (
        "429",
        _status(429, {"error": "slow"}),
        "{name} rate limit or quota reached (HTTP 429)",
    ),
    ("500", _status(500, {"message": "upstream down"}), "HTTP 500: upstream down"),
    ("500-empty", _status(500, text=""), "HTTP 500"),
    ("timeout", _raises(httpx.ReadTimeout, "read timed out"), "request timed out"),
    (
        "not-json",
        _status(200, text="<html>oops</html>"),
        "invalid JSON response from {name}",
    ),
    ("json-list", _status(200, ["a"]), "unexpected response format from {name}"),
    (
        "no-results",
        _status(200, {"other": 1}),
        "unexpected response format from {name}",
    ),
]


@pytest.mark.parametrize(("provider_cls", "name"), HOSTED_PROVIDERS)
@pytest.mark.parametrize(
    ("case", "handler", "expected"), ERROR_CASES, ids=[c[0] for c in ERROR_CASES]
)
async def test_hosted_error_matrix(
    provider_cls: type[HostedSearchProvider],
    name: str,
    case: str,
    handler: Handler,
    expected: str,
) -> None:
    """Every provider maps statuses, timeouts and bad bodies the same way."""
    if case == "no-results" and provider_cls is BraveProvider:
        pytest.skip("Brave treats a body without `web` as no results.")
    with pytest.raises(SearchProviderError) as exc_info:
        await _run(provider_cls, handler)
    assert str(exc_info.value) == expected.format(name=name)


@pytest.mark.parametrize("provider_cls", HOSTED_CLASSES)
async def test_hosted_errors_scrub_key(
    aldwin_home: Path, provider_cls: type[HostedSearchProvider]
) -> None:
    """The key never appears in errors or tool output."""
    handlers = [
        _status(500, {"message": f"bad token {KEY}"}),
        _raises(httpx.ConnectError, f"failed with token {KEY}"),
    ]
    for handler in handlers:
        with pytest.raises(SearchProviderError) as exc_info:
            await _run(provider_cls, handler)
        assert KEY not in str(exc_info.value)
        assert "[redacted]" in str(exc_info.value)

        reset_web_search_turn_budget()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tool = build_web_search_tool(
                home=aldwin_home, provider=provider_cls(KEY, client=client)
            )
            res = await tool.call({"query": "x"})
        assert res.ok is False
        assert KEY not in str(res)
        assert KEY not in res.summary


@pytest.mark.parametrize("provider_cls", HOSTED_CLASSES)
async def test_hosted_slices_to_limit(provider_cls: type[HostedSearchProvider]) -> None:
    """Ten hits with limit 3 return three, and the request asks for three."""
    seen, handler = _capture(_wrap_hits(provider_cls, _hits(10)))
    results = await _run(provider_cls, handler, limit=3)
    assert len(results) == 3

    request = seen[0]
    if provider_cls is BraveProvider:
        assert request.url.params["count"] == "3"
    else:
        sent = json.loads(request.content)
        requested = (
            sent.get("max_results")
            or sent.get("numResults")
            or sent.get("limit")
            or sent["advanced_settings"]["max_results"]
        )
        assert requested == 3


@pytest.mark.parametrize("provider_cls", HOSTED_CLASSES)
async def test_hosted_drops_urlless_and_titles_fallback(
    provider_cls: type[HostedSearchProvider],
) -> None:
    """URL-less hits dropped; blank title falls back to URL; spaces collapse."""
    hits = [
        {"title": "No URL", "url": "", "snippet": "x"},
        {"title": "  ", "url": "https://kept.example", "snippet": "a \n\n  b"},
    ]
    _, handler = _capture(_wrap_hits(provider_cls, hits))
    results = await _run(provider_cls, handler)
    assert results == [
        SearchResult(
            title="https://kept.example", url="https://kept.example", snippet="a b"
        )
    ]


@pytest.mark.parametrize(("limit", "expected"), [(0, 1), (99, 10)])
async def test_hosted_limit_is_clamped(limit: int, expected: int) -> None:
    """Providers clamp limit to 1..10 even when called directly."""
    seen, handler = _capture({"results": []})
    await _run(PerplexityProvider, handler, limit=limit)
    assert json.loads(seen[0].content)["max_results"] == expected


def test_empty_key_rejected() -> None:
    with pytest.raises(ValueError, match="Brave API key must not be empty"):
        BraveProvider("   ")


# Key resolution and factory


def test_resolve_search_api_key_order(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env → alias env → secrets.yaml → injected snapshot → None."""
    save_secrets(SecretsConfig.model_validate({"brave_api_key": "file"}), aldwin_home)
    injected = SecretsConfig.model_validate({"brave_api_key": "injected"})
    sources = hosted_mod.load_search_secrets(aldwin_home, injected)
    aliases = ("BRAVE_SEARCH_API_KEY",)

    monkeypatch.setenv("BRAVE_API_KEY", "env")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "alias")
    assert resolve_search_api_key("brave", sources, env_aliases=aliases) == "env"

    monkeypatch.setenv("BRAVE_API_KEY", "   ")
    assert resolve_search_api_key("brave", sources, env_aliases=aliases) == "alias"

    monkeypatch.delenv("BRAVE_SEARCH_API_KEY")
    assert resolve_search_api_key("brave", sources, env_aliases=aliases) == "file"

    only_injected = hosted_mod.load_search_secrets(None, injected)
    assert resolve_search_api_key("brave", only_injected) == "injected"
    assert resolve_search_api_key("brave", ()) is None


def test_build_reads_secrets_once(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One secrets.yaml read per factory call, not one per provider."""
    calls: list[Path | None] = []
    real = hosted_mod.load_secrets

    def spy(home: Path | None = None) -> SecretsConfig:
        calls.append(home)
        return real(home)

    monkeypatch.setattr(hosted_mod, "load_secrets", spy)

    assert build_search_provider(home=aldwin_home) is None
    assert len(calls) == 1

    calls.clear()
    assert ready_search_provider_names(home=aldwin_home) == ()
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"EXA_API_KEY": "k"}, ExaProvider),
        ({"TAVILY_API_KEY": "k", "BRAVE_API_KEY": "k"}, TavilyProvider),
        (
            {"SEARXNG_URL": "http://127.0.0.1:8080", "BRAVE_API_KEY": "k"},
            SearxngProvider,
        ),
        ({"BRAVE_API_KEY": "k", "EXA_API_KEY": "k"}, BraveProvider),
        ({"PERPLEXITY_API_KEY": "k"}, PerplexityProvider),
        ({"PARALLEL_API_KEY": "k"}, ParallelProvider),
        ({"FIRECRAWL_API_KEY": "k"}, FirecrawlProvider),
    ],
    ids=[
        "exa-only",
        "tavily-over-brave",
        "searxng-over-brave",
        "brave-over-exa",
        "perplexity",
        "parallel",
        "firecrawl",
    ],
)
def test_build_search_provider_hosted_auto_matrix(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    expected: type,
) -> None:
    """Auto picks the first ready backend in the settled order."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert isinstance(build_search_provider(home=aldwin_home), expected)


def test_auto_reads_key_from_secrets_file(aldwin_home: Path) -> None:
    """A key saved in secrets.yaml is enough for auto to pick that provider."""
    save_secrets(
        SecretsConfig.model_validate({"firecrawl_api_key": SecretStr("fc-file")}),
        aldwin_home,
    )
    provider = build_search_provider(home=aldwin_home)
    assert isinstance(provider, FirecrawlProvider)
    assert provider.secret == "fc-file"


NOT_SET_UP = {
    "brave": (
        "Brave search isn't set up. Ask the user to run /search to connect Brave."
    ),
    "exa": ("Exa search isn't set up. Ask the user to run /search to connect Exa."),
    "perplexity": (
        "Perplexity search isn't set up. Ask the user to run /search to connect "
        "Perplexity."
    ),
    "parallel": (
        "Parallel search isn't set up. Ask the user to run /search to connect Parallel."
    ),
    "firecrawl": (
        "Firecrawl search isn't set up. Ask the user to run /search to connect "
        "Firecrawl."
    ),
    "tavily": (
        "Tavily search isn't set up. Ask the user to run /search to connect Tavily."
    ),
}


@pytest.mark.parametrize("provider", sorted(NOT_SET_UP))
async def test_explicit_hosted_provider_not_set_up(
    aldwin_home: Path, provider: str
) -> None:
    """An explicit provider without a key says how to set it up."""
    atomic_write(aldwin_home / "config.yaml", f"search:\n  provider: {provider}\n")
    res = await build_web_search_tool(home=aldwin_home).call({"query": "x"})
    assert res.ok is False
    assert str(res) == NOT_SET_UP[provider]
    assert res.summary == "not set up"


def test_switch_and_describe_hosted(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/search refuses an unready provider, switches to a ready one."""
    refused = switch_search_provider("perplexity", home=aldwin_home)
    assert refused.ok is False
    assert refused.message == (
        "perplexity isn't ready. Connect it with /search, or set PERPLEXITY_API_KEY."
    )

    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-k")
    switched = switch_search_provider("perplexity", home=aldwin_home)
    assert switched.ok is True
    assert switched.message == "Search · Perplexity"

    status = describe_active_search(home=aldwin_home)
    lines = status.splitlines()
    assert lines[0] == "Search · Perplexity"
    assert "  perplexity ready · active" in lines
    assert "  auto       first ready backend" in lines
    for name in ("brave", "exa", "firecrawl", "parallel", "searxng", "tavily"):
        row = next(line for line in lines if line.startswith(f"  {name} "))
        assert row.startswith(f"  {name:<10} not ready - ")


def test_web_search_parallel_safe_true_for_hosted(aldwin_home: Path) -> None:
    """Hosted providers may run in parallel; the tool says so."""
    tool = build_web_search_tool(home=aldwin_home, provider=BraveProvider(KEY))
    assert tool.parallel_safe is True


# Architecture


def test_no_search_vendor_sdk_imports() -> None:
    """Hosted search uses plain httpx, never a vendor SDK."""
    forbidden = ("exa_py", "firecrawl", "parallel", "tavily", "perplexity")
    root = Path(aldwin.__file__).parent
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.split(".")[0] in forbidden:
                    violations.append(f"{path}: {module}")
    assert violations == []


@pytest.mark.parametrize(
    "provider", ["auto", "searxng", "tavily", "brave", "exa", "perplexity"]
)
def test_model_facing_setup_text_points_at_search_command(provider: str) -> None:
    """The model is told to send the user to /search, never to edit a file."""
    from aldwin.core.tools.web_search.shared import not_set_up_message

    message, summary = not_set_up_message(provider)  # type: ignore[arg-type]
    assert "/search" in message
    assert summary == "not set up"
    for path_hint in ("secrets.yaml", "config.yaml", "~/.aldwin", "_API_KEY"):
        assert path_hint not in message
