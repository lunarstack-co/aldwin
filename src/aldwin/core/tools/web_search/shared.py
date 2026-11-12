"""Shared types, constants, factory, switch helpers, and the web_search tool."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, get_args, runtime_checkable

import httpx
from agentling import Tool

from aldwin.core.settings import (
    AldwinConfig,
    SearchConfig,
    SearchProviderName,
    SecretsConfig,
    load_config,
    update_config_search_provider,
)
from aldwin.core.tools import ToolOutput
from aldwin.core.tools import display as core_display

if TYPE_CHECKING:
    from aldwin.core.tools.web_search.hosted import HostedSearchProvider

SEARCH_TIMEOUT_SECONDS: Final[float] = 15.0
MAX_QUERY_CHARS: Final[int] = 400
MIN_LIMIT: Final[int] = 1
MAX_LIMIT: Final[int] = 10
DEFAULT_LIMIT: Final[int] = 5
MAX_SNIPPET_CHARS: Final[int] = 200
DEFAULT_MAX_SEARCHES_PER_TURN: Final[int] = 5


@dataclass(slots=True)
class _TurnSearchBudget:
    used: int = 0


# Shared mutable object so increments survive gather/wait_for child tasks.
_turn_budget: ContextVar[_TurnSearchBudget | None] = ContextVar(
    "aldwin_web_search_turn_budget",
    default=None,
)

NO_KEY_MESSAGE: Final[str] = (
    "Web search isn't set up. Ask the user to run /search to connect a search provider."
)

IMPLEMENTED_SEARCH_PROVIDERS: Final[frozenset[str]] = frozenset(
    {"tavily", "searxng", "brave", "exa", "perplexity", "parallel", "firecrawl"}
)

# Auto order: Tavily key, then SearXNG URL, then the other hosted keys.
AUTO_PROVIDER_ORDER: Final[tuple[str, ...]] = (
    "tavily",
    "searxng",
    "brave",
    "exa",
    "perplexity",
    "parallel",
    "firecrawl",
)

_SEARCH_PROVIDER_ALIASES: Final[dict[str, str]] = {
    "searx": "searxng",
    "searxng": "searxng",
    "tavily": "tavily",
    "auto": "auto",
}

_SEARXNG_SETUP_HINT: Final[str] = (
    "Start SearXNG with JSON on :8080 (found automatically), or connect it "
    "with /search."
)


def hosted_provider_classes() -> dict[str, type[HostedSearchProvider]]:
    """Hosted backends by catalogue name (imported lazily: they import this module)."""
    from aldwin.core.tools.web_search.brave import BraveProvider
    from aldwin.core.tools.web_search.exa import ExaProvider
    from aldwin.core.tools.web_search.firecrawl import FirecrawlProvider
    from aldwin.core.tools.web_search.parallel import ParallelProvider
    from aldwin.core.tools.web_search.perplexity import PerplexityProvider
    from aldwin.core.tools.web_search.tavily import TavilyProvider

    classes: tuple[type[HostedSearchProvider], ...] = (
        TavilyProvider,
        BraveProvider,
        ExaProvider,
        PerplexityProvider,
        ParallelProvider,
        FirecrawlProvider,
    )
    return {cls.provider_id: cls for cls in classes}


def _setup_hint(name: str) -> str:
    if name == "searxng":
        return _SEARXNG_SETUP_HINT
    hosted = hosted_provider_classes().get(name)
    return hosted.setup_hint() if hosted is not None else "Configure it first."


def _display_name(name: str) -> str:
    if name == "searxng":
        return "SearXNG"
    hosted = hosted_provider_classes().get(name)
    return hosted.name if hosted is not None else name


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    snippet: str


class SearchProviderError(Exception):
    """Raised by a `SearchProvider` when a search request fails."""


@runtime_checkable
class SearchProvider(Protocol):
    """Protocol for pluggable web search backends."""

    async def search(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
    ) -> list[SearchResult]:
        """Return up to `limit` search results for `query`."""
        ...


def scrub_key(text: str, api_key: str) -> str:
    """Remove `api_key` from `text` if present and collapse whitespace."""
    cleaned = text.replace(api_key, "[redacted]") if api_key else text
    return " ".join(cleaned.split())


def truncate_snippet(
    text: str,
    max_chars: int = MAX_SNIPPET_CHARS,
) -> str:
    """Collapse whitespace and cap length with ``...`` to keep tool output small."""
    cleaned = " ".join(text.split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars] + "..."


def is_local_search_provider(provider: SearchProvider | None) -> bool:
    """Return True for self-hosted backends that fan out to public engines."""
    if provider is None:
        return False
    return bool(getattr(provider, "is_local", False))


def reset_web_search_turn_budget() -> None:
    """Start a fresh per-turn ``web_search`` budget (host calls this each turn)."""
    _turn_budget.set(_TurnSearchBudget())


def consume_web_search_turn_budget(max_calls: int) -> str | None:
    """Count one call; return an error message once the turn's cap is reached."""
    budget = _turn_budget.get()
    if budget is None:
        budget = _TurnSearchBudget()
        _turn_budget.set(budget)
    ceiling = max(1, int(max_calls))
    if budget.used >= ceiling:
        return (
            f"Web search limit reached for this turn ({ceiling}). "
            "Summarize what you already have, or continue in a new message."
        )
    budget.used += 1
    return None


def resolve_web_searches_limit(
    home: Path | None,
    config: AldwinConfig | None,
) -> int:
    """Return ``limits.web_searches`` from injected config or disk."""
    if config is not None:
        return int(config.limits.web_searches)
    if home is not None:
        return int(load_config(home).limits.web_searches)
    return DEFAULT_MAX_SEARCHES_PER_TURN


def load_search_config(
    home: Path | None,
    config: AldwinConfig | None,
) -> SearchConfig:
    """Return search settings from an injected config or disk."""
    if config is not None:
        return config.search
    if home is not None:
        return load_config(home).search
    return SearchConfig()


def try_build_provider(
    name: str,
    *,
    home: Path | None,
    secrets: SecretsConfig | None,
    config: AldwinConfig | None,
    timeout: float,
    http_client: httpx.AsyncClient | None,
    detect_local: bool = False,
    secret_sources: tuple[SecretsConfig, ...] | None = None,
) -> SearchProvider | None:
    """Build one named provider when implemented and credentials/URL are present.

    Local SearXNG auto-detect is opt-in (``detect_local=True``) so the tool path
    never does a synchronous HTTP probe on the event loop. CLI status/switch
    may pass ``detect_local=True``. Callers building several providers pass
    ``secret_sources`` so ``secrets.yaml`` is read once.
    """
    if name not in IMPLEMENTED_SEARCH_PROVIDERS:
        return None

    hosted = hosted_provider_classes().get(name)
    if hosted is not None:
        from aldwin.core.tools.web_search.hosted import (
            load_search_secrets,
            resolve_search_api_key,
        )

        sources = (
            secret_sources
            if secret_sources is not None
            else load_search_secrets(home, secrets)
        )
        api_key = resolve_search_api_key(name, sources, env_aliases=hosted.env_aliases)
        if not api_key:
            return None
        return hosted(api_key, client=http_client, timeout=timeout)

    if name == "searxng":
        from aldwin.core.tools.web_search.searxng import (
            SearxngProvider,
            resolve_searxng_url,
        )

        base_url = resolve_searxng_url(home, config=config, detect_local=detect_local)
        if not base_url:
            return None
        return SearxngProvider(base_url, client=http_client, timeout=timeout)

    return None


def build_search_provider(
    home: Path | None = None,
    secrets: SecretsConfig | None = None,
    *,
    config: AldwinConfig | None = None,
    timeout: float = SEARCH_TIMEOUT_SECONDS,
    http_client: httpx.AsyncClient | None = None,
    detect_local: bool = False,
) -> SearchProvider | None:
    """Select a SearchProvider from config and secrets.

    When search.provider is auto, walk the catalogue order and return the first
    backend that has credentials or a URL.
    """
    from aldwin.core.tools.web_search.hosted import load_search_secrets

    if config is not None:
        effective_config = config
    elif home is not None:
        effective_config = load_config(home)
    else:
        effective_config = AldwinConfig()
    search = effective_config.search

    if search.provider == "auto":
        sources = load_search_secrets(home, secrets)
        for provider_name in AUTO_PROVIDER_ORDER:
            built = try_build_provider(
                provider_name,
                home=home,
                secrets=secrets,
                config=effective_config,
                timeout=timeout,
                http_client=http_client,
                detect_local=detect_local,
                secret_sources=sources,
            )
            if built is not None:
                return built
        return None

    return try_build_provider(
        search.provider,
        home=home,
        secrets=secrets,
        config=effective_config,
        timeout=timeout,
        http_client=http_client,
        detect_local=detect_local,
    )


def active_search_provider_name(
    home: Path | None = None,
    secrets: SecretsConfig | None = None,
    *,
    config: AldwinConfig | None = None,
    detect_local: bool = True,
) -> str | None:
    """Return the display name of the search backend that would run, or None.

    Defaults to ``detect_local=True`` for CLI status; the tool path should pass
    ``detect_local=False`` via ``build_search_provider``.
    """
    provider = build_search_provider(
        home, secrets, config=config, detect_local=detect_local
    )
    if provider is None:
        return None
    label = getattr(provider, "name", None)
    return label if isinstance(label, str) and label.strip() else None


def switchable_search_providers() -> tuple[str, ...]:
    """Names accepted by ``/search`` today: ``auto`` plus implemented backends."""
    return ("auto", *sorted(IMPLEMENTED_SEARCH_PROVIDERS))


def normalize_search_provider_arg(raw: str) -> str | None:
    """Map a user argument to a canonical provider name, or None if unknown."""
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    if cleaned in _SEARCH_PROVIDER_ALIASES:
        return _SEARCH_PROVIDER_ALIASES[cleaned]
    if cleaned in get_args(SearchProviderName):
        return cleaned
    return None


def ready_search_provider_names(
    home: Path | None = None,
    secrets: SecretsConfig | None = None,
    *,
    config: AldwinConfig | None = None,
) -> tuple[str, ...]:
    """Implemented backends that currently have credentials or a URL."""
    from aldwin.core.tools.web_search.hosted import load_search_secrets

    if config is not None:
        effective = config
    elif home is not None:
        effective = load_config(home)
    else:
        effective = AldwinConfig()
    sources = load_search_secrets(home, secrets)
    ready: list[str] = []
    for name in sorted(IMPLEMENTED_SEARCH_PROVIDERS):
        built = try_build_provider(
            name,
            home=home,
            secrets=secrets,
            config=effective,
            timeout=SEARCH_TIMEOUT_SECONDS,
            http_client=None,
            detect_local=True,
            secret_sources=sources,
        )
        if built is not None:
            ready.append(name)
    return tuple(ready)


@dataclass(frozen=True, slots=True)
class SearchSwitchResult:
    """Outcome of inspecting or switching the search backend."""

    ok: bool
    message: str
    provider: str | None = None


def describe_active_search(
    home: Path | None = None,
    secrets: SecretsConfig | None = None,
    *,
    config: AldwinConfig | None = None,
) -> str:
    """Status for bare ``/search``: re-detect local SearXNG and list providers."""
    from aldwin.core.tools.web_search.searxng import (
        clear_searxng_detection_cache,
        detect_local_searxng,
        resolve_searxng_url,
    )

    clear_searxng_detection_cache()
    detected = detect_local_searxng(force=True)

    if config is not None:
        effective = config
    elif home is not None:
        effective = load_config(home)
    else:
        effective = AldwinConfig()

    label = active_search_provider_name(home, secrets, config=effective)
    ready = set(ready_search_provider_names(home, secrets, config=effective))
    configured = effective.search.provider

    if label is None:
        header = "Search · not set up"
    elif configured == "auto":
        header = f"Search · {label} (auto)"
    else:
        header = f"Search · {label}"

    lines = [header, "Providers:"]
    for name in switchable_search_providers():
        if name == "auto":
            marker = "· current" if configured == "auto" else ""
            detail = "first ready backend"
            lines.append(f"  {'auto':<10} {detail}{f' {marker}' if marker else ''}")
            continue

        if name in ready:
            extra = ""
            if name == "searxng" and detected:
                extra = f" · {detected}"
            elif name == "searxng":
                url = resolve_searxng_url(home, config=effective, detect_local=False)
                if url:
                    extra = f" · {url}"
            active = " · active" if label and _display_name(name) == label else ""
            lines.append(f"  {name:<10} ready{extra}{active}")
        else:
            lines.append(f"  {name:<10} not ready - {_setup_hint(name)}")

    lines.append("Switch with /search <name> (saved in config.yaml).")
    return "\n".join(lines)


def switch_search_provider(
    raw_name: str,
    home: Path | None = None,
    secrets: SecretsConfig | None = None,
    *,
    config: AldwinConfig | None = None,
) -> SearchSwitchResult:
    """Validate, optionally require readiness, and persist ``search.provider``."""
    canonical = normalize_search_provider_arg(raw_name)
    if canonical is None:
        names = ", ".join(switchable_search_providers())
        return SearchSwitchResult(
            ok=False,
            message=f"Unknown search provider {raw_name.strip()!r}. Use: {names}",
        )

    if config is not None:
        effective = config
    elif home is not None:
        effective = load_config(home)
    else:
        effective = AldwinConfig()

    if canonical != "auto":
        built = try_build_provider(
            canonical,
            home=home,
            secrets=secrets,
            config=effective,
            timeout=SEARCH_TIMEOUT_SECONDS,
            http_client=None,
            detect_local=True,
        )
        if built is None:
            return SearchSwitchResult(
                ok=False,
                message=f"{canonical} isn't ready. {_setup_hint(canonical)}",
                provider=canonical,
            )

    try:
        update_config_search_provider(canonical, home=home)
    except Exception as exc:
        return SearchSwitchResult(
            ok=False,
            message=f"Could not save search.provider: {exc}",
            provider=canonical,
        )

    if canonical == "auto":
        label = active_search_provider_name(home, secrets)
        if label is None:
            return SearchSwitchResult(
                ok=True,
                message="Search · auto (not set up)",
                provider="auto",
            )
        return SearchSwitchResult(
            ok=True,
            message=f"Search · {label} (auto)",
            provider="auto",
        )

    label = active_search_provider_name(home, secrets) or canonical
    return SearchSwitchResult(
        ok=True,
        message=f"Search · {label}",
        provider=canonical,
    )


def not_set_up_message(provider: SearchProviderName) -> tuple[str, str]:
    """Return ``(message, summary)`` when the factory could not build a provider."""
    from aldwin.core.tools.web_search.searxng import SEARXNG_NOT_SET_UP_MESSAGE

    if provider == "auto":
        return NO_KEY_MESSAGE, "not set up"
    if provider == "searxng":
        return SEARXNG_NOT_SET_UP_MESSAGE, "not set up"
    title = _display_name(provider)
    message = (
        f"{title} search isn't set up. Ask the user to run /search to connect {title}."
    )
    return message, "not set up"


def scrub_secret_for_provider(provider: SearchProvider) -> str:
    """Return a secret string to scrub from error text, if the backend has one."""
    secret = getattr(provider, "secret", "")
    return secret if isinstance(secret, str) else ""


class WebSearchTool(Tool):
    """Search the web and return numbered results with titles, URLs, and snippets."""

    name = "web_search"
    description = (
        "Search the web for current information and return up to 10 numbered "
        "results with title, URL, and a short snippet (compact text, not JSON). "
        "Prefer one broad query over many narrow ones."
    )
    timeout = None
    # Cap tool text so long provider payloads cannot fill the context window.
    max_output_chars = 5_000
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (1-400 characters).",
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Maximum number of results to return "
                    f"(1-{MAX_LIMIT}, default {DEFAULT_LIMIT})."
                ),
                "default": DEFAULT_LIMIT,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        home: Path | None = None,
        secrets: SecretsConfig | None = None,
        *,
        provider: SearchProvider | None = None,
        config: AldwinConfig | None = None,
        timeout: float = SEARCH_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._home = home
        self._secrets = secrets
        self._provider = provider
        self._config = config
        self._timeout = timeout
        self._http_client = http_client

    def _resolve_provider(self) -> SearchProvider | None:
        if self._provider is not None:
            return self._provider
        return build_search_provider(
            self._home,
            self._secrets,
            config=self._config,
            timeout=self._timeout,
            http_client=self._http_client,
            detect_local=False,
        )

    @property
    def parallel_safe(self) -> bool:  # type: ignore[override]
        """Hosted providers may run in parallel; local metasearch must not burst."""
        provider = self._resolve_provider()
        if provider is None:
            # May still resolve to local SearXNG via detect in forward; stay serial.
            return False
        return not is_local_search_provider(provider)

    async def forward(  # type: ignore[override]
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
    ) -> ToolOutput:
        if not isinstance(query, str) or "\x00" in query:
            return ToolOutput(
                "Invalid search query: query must be a valid string.",
                summary="invalid query",
                ok=False,
            )

        cleaned_query = " ".join(query.split())
        if not cleaned_query or len(cleaned_query) > MAX_QUERY_CHARS:
            return ToolOutput(
                f"Invalid search query: must be between 1 and {MAX_QUERY_CHARS} "
                f"characters (got {len(cleaned_query)}).",
                summary="invalid query",
                ok=False,
            )

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < MIN_LIMIT
            or limit > MAX_LIMIT
        ):
            return ToolOutput(
                f"Invalid search limit: must be an integer between {MIN_LIMIT} and "
                f"{MAX_LIMIT} (got {limit!r}).",
                summary="invalid limit",
                ok=False,
            )

        budget_error = consume_web_search_turn_budget(
            resolve_web_searches_limit(self._home, self._config)
        )
        if budget_error is not None:
            return ToolOutput(
                budget_error,
                summary="search limit reached",
                ok=False,
            )

        provider = self._resolve_provider()
        if provider is None:
            provider = await asyncio.to_thread(
                build_search_provider,
                self._home,
                self._secrets,
                config=self._config,
                timeout=self._timeout,
                http_client=self._http_client,
                detect_local=True,
            )
        if provider is None:
            search = load_search_config(self._home, self._config)
            message, summary = not_set_up_message(search.provider)
            return ToolOutput(message, summary=summary, ok=False)

        scrub = scrub_secret_for_provider(provider)

        try:
            results = await asyncio.wait_for(
                provider.search(cleaned_query, limit),
                timeout=self._timeout,
            )
        except TimeoutError:
            return ToolOutput(
                f"search failed: request timed out after {self._timeout:g}s",
                summary="search failed: timed out",
                ok=False,
            )
        except Exception as exc:
            reason = scrub_key(str(exc), scrub) or type(exc).__name__
            return ToolOutput(
                f"search failed: {reason}",
                summary=f"search failed: {reason}",
                ok=False,
            )

        if not results:
            return ToolOutput(
                "No results found.",
                summary="0 results",
                ok=True,
                display=core_display.search(query=cleaned_query, results=[]),
            )

        formatted: list[str] = []
        display_hits: list[dict[str, str]] = []
        for idx, item in enumerate(results, start=1):
            title = " ".join(item.title.split()) or item.url.strip()
            url = item.url.strip()
            snippet = truncate_snippet(item.snippet)
            display_hits.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet,
                }
            )
            if idx <= limit:
                formatted.append(f"{idx}. {title} - {url}\n   {snippet}")

        count = len(formatted)
        summary = "1 result" if count == 1 else f"{count} results"
        return ToolOutput(
            "\n".join(formatted),
            summary=summary,
            ok=True,
            display=core_display.search(
                query=cleaned_query, results=display_hits[:limit]
            ),
        )


def build_web_search_tool(
    home: Path | None = None,
    secrets: SecretsConfig | None = None,
    *,
    provider: SearchProvider | None = None,
    config: AldwinConfig | None = None,
    timeout: float = SEARCH_TIMEOUT_SECONDS,
    http_client: httpx.AsyncClient | None = None,
) -> WebSearchTool:
    """Construct the WebSearchTool."""
    return WebSearchTool(
        home=home,
        secrets=secrets,
        provider=provider,
        config=config,
        timeout=timeout,
        http_client=http_client,
    )
