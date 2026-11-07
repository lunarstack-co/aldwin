"""Connecting, switching and disconnecting search providers from ``/search``."""

from __future__ import annotations

import stat
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from aldwin.core.home import atomic_write
from aldwin.core.settings import SecretsConfig, load_config, load_secrets, save_secrets
from aldwin.core.tools.web_search import (
    BraveProvider,
    KeyCheck,
    TavilyProvider,
    build_search_provider,
    build_web_search_tool,
    reset_web_search_turn_budget,
)
from aldwin.core.tools.web_search import searxng as searxng_mod
from aldwin.core.tools.web_search.connect import (
    DisconnectOutcome,
    connect_search_key,
    connect_searxng,
    disconnect_search,
    search_overview,
)

KEY = "brave-key-abcdef1234567890"


def _transport(status: int, body: object | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(status, json=body if body is not None else {})
    )


def _secrets_bytes(home: Path) -> bytes | None:
    path = home / "secrets.yaml"
    return path.read_bytes() if path.exists() else None


@pytest.fixture(autouse=True)
def _no_searxng_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SEARXNG_URL", "SEARXNG_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(searxng_mod, "_LOCAL_SEARXNG_CANDIDATES", ())
    searxng_mod.clear_searxng_detection_cache()


async def test_connect_saves_key_keeps_others_and_uses_provider(
    aldwin_home: Path,
) -> None:
    """A passing check saves the key (mode 0600), keeps other keys, selects it."""
    save_secrets(SecretsConfig(openai_api_key=SecretStr("sk-openai-keep")), aldwin_home)

    result = await connect_search_key(
        "brave", KEY, home=aldwin_home, transport=_transport(200, {"web": {}})
    )

    assert result.check.outcome is KeyCheck.OK
    assert result.env_override is None
    secrets = load_secrets(aldwin_home)
    assert secrets.get_secret("brave_api_key") == KEY
    assert secrets.get_secret("openai_api_key") == "sk-openai-keep"
    mode = stat.S_IMODE((aldwin_home / "secrets.yaml").stat().st_mode)
    assert mode == 0o600
    assert load_config(aldwin_home).search.provider == "brave"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, KeyCheck.REJECTED),
        (402, KeyCheck.NO_CREDITS),
        (429, KeyCheck.RATE_LIMITED),
        (503, KeyCheck.UNREACHABLE),
        (418, KeyCheck.UNEXPECTED),
    ],
)
async def test_failed_check_writes_nothing(
    aldwin_home: Path, status: int, expected: KeyCheck
) -> None:
    """Any result but OK leaves secrets.yaml and config.yaml exactly as they were."""
    save_secrets(SecretsConfig(openai_api_key=SecretStr("sk-openai-keep")), aldwin_home)
    before_secrets = _secrets_bytes(aldwin_home)
    before_config = (aldwin_home / "config.yaml").read_bytes()

    result = await connect_search_key(
        "tavily", "tvly-bad-key-123456", home=aldwin_home, transport=_transport(status)
    )

    assert result.check.outcome is expected
    assert _secrets_bytes(aldwin_home) == before_secrets
    assert (aldwin_home / "config.yaml").read_bytes() == before_config


async def test_connect_strips_whitespace(aldwin_home: Path) -> None:
    """A key pasted with spaces or a newline is checked and saved trimmed."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    await connect_search_key(
        "tavily",
        "  tvly-padded-key-123456 \n",
        home=aldwin_home,
        transport=httpx.MockTransport(handler),
    )
    assert seen[0].headers["Authorization"] == "Bearer tvly-padded-key-123456"
    assert load_secrets(aldwin_home).get_secret("tavily") == "tvly-padded-key-123456"


async def test_connect_reports_env_override(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An environment variable with another key still wins, so the user is told."""
    monkeypatch.setenv("BRAVE_API_KEY", "brave-from-env-000000")
    result = await connect_search_key(
        "brave", KEY, home=aldwin_home, transport=_transport(200, {})
    )
    assert result.env_override == "BRAVE_API_KEY"


async def test_connect_searxng_probes_then_saves(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reachable instance is saved with comments kept; a dead one writes nothing."""
    atomic_write(
        aldwin_home / "config.yaml",
        "# my settings\nmodel: gpt-4o\nsearch:\n  # pick one\n  provider: auto\n",
    )
    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", lambda *_a, **_k: False)
    dead = await connect_searxng("http://127.0.0.1:9999", home=aldwin_home)
    assert dead.check.outcome is KeyCheck.UNREACHABLE
    assert dead.check.detail == "no SearXNG with JSON at http://127.0.0.1:9999"
    assert load_config(aldwin_home).search.searxng_url is None

    probed: list[str] = []

    def probe(url: str, **_kwargs: object) -> bool:
        probed.append(url)
        return True

    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", probe)
    ok = await connect_searxng("http://127.0.0.1:8888/", home=aldwin_home)
    assert ok.check.outcome is KeyCheck.OK
    assert probed == ["http://127.0.0.1:8888"]
    config = load_config(aldwin_home)
    assert config.search.searxng_url == "http://127.0.0.1:8888"
    assert config.search.provider == "searxng"
    text = (aldwin_home / "config.yaml").read_text()
    assert "# my settings" in text and "# pick one" in text


async def test_connect_searxng_rejects_non_http(aldwin_home: Path) -> None:
    result = await connect_searxng("ftp://box", home=aldwin_home)
    assert result.check.outcome is KeyCheck.UNEXPECTED
    assert load_config(aldwin_home).search.searxng_url is None


def test_disconnect_outcomes(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File keys are removed; env keys and never-connected providers are reported."""
    save_secrets(SecretsConfig.model_validate({"exa_api_key": "exa-key"}), aldwin_home)
    assert (
        disconnect_search("exa", home=aldwin_home).outcome is DisconnectOutcome.REMOVED
    )
    assert load_secrets(aldwin_home).get_secret("exa_api_key") is None

    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-from-env")
    env_only = disconnect_search("parallel", home=aldwin_home)
    assert env_only.outcome is DisconnectOutcome.ENV_ONLY
    assert env_only.env_var == "PARALLEL_API_KEY"

    missing = disconnect_search("firecrawl", home=aldwin_home)
    assert missing.outcome is DisconnectOutcome.NOT_CONNECTED


def test_disconnect_searxng(aldwin_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A saved address is cleared; a detected one cannot be disconnected."""
    atomic_write(
        aldwin_home / "config.yaml",
        "model: gpt-4o\nsearch:\n  provider: searxng\n  searxng_url: http://h:8080\n",
    )
    result = disconnect_search("searxng", home=aldwin_home)
    assert result.outcome is DisconnectOutcome.REMOVED
    config = load_config(aldwin_home)
    assert config.search.searxng_url is None
    assert config.search.provider == "auto"

    monkeypatch.setattr(
        searxng_mod, "_LOCAL_SEARXNG_CANDIDATES", ("http://127.0.0.1:8080",)
    )
    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", lambda *_a, **_k: True)
    searxng_mod.clear_searxng_detection_cache()
    detected = disconnect_search("searxng", home=aldwin_home)
    assert detected.outcome is DisconnectOutcome.DETECTED


def test_disconnecting_active_provider_falls_back_to_auto(aldwin_home: Path) -> None:
    save_secrets(SecretsConfig.model_validate({"brave_api_key": KEY}), aldwin_home)
    atomic_write(aldwin_home / "config.yaml", "search:\n  provider: brave\n")
    disconnect_search("brave", home=aldwin_home)
    assert load_config(aldwin_home).search.provider == "auto"


async def test_disconnect_takes_effect_on_next_search(aldwin_home: Path) -> None:
    """A session's secrets snapshot never keeps a disconnected key alive."""
    save_secrets(SecretsConfig.model_validate({"brave_api_key": KEY}), aldwin_home)
    snapshot = load_secrets(aldwin_home)
    assert isinstance(build_search_provider(aldwin_home, snapshot), BraveProvider)

    disconnect_search("brave", home=aldwin_home)

    assert build_search_provider(aldwin_home, snapshot) is None
    reset_web_search_turn_budget()
    res = await build_web_search_tool(home=aldwin_home, secrets=snapshot).call(
        {"query": "x"}
    )
    assert res.ok is False
    assert res.summary == "not set up"


def test_overview_reports_every_provider(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows come in automatic order with source, masked key and the active flag."""
    save_secrets(
        SecretsConfig.model_validate({"tavily_api_key": "tvly-file-key-8f2a"}),
        aldwin_home,
    )
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-env-key-77aa")

    overview = search_overview(aldwin_home)

    assert overview.setting == "auto"
    assert overview.active == "tavily"
    assert [i.name for i in overview.items] == [
        "tavily",
        "searxng",
        "brave",
        "exa",
        "perplexity",
        "parallel",
        "firecrawl",
    ]
    rows = {i.name: i for i in overview.items}
    assert rows["tavily"].connected and rows["tavily"].source == "file"
    assert rows["tavily"].active
    assert rows["tavily"].detail.endswith("8f2a")
    assert "tvly-file-key-8f2a" not in rows["tavily"].detail
    assert rows["brave"].source == "env"
    assert rows["brave"].env_var == "BRAVE_SEARCH_API_KEY"
    assert rows["brave"].check_note == "key check uses one Brave query"
    assert not rows["exa"].connected and rows["exa"].detail == ""
    assert rows["exa"].env_var == "EXA_API_KEY"
    assert rows["searxng"].kind == "local" and not rows["searxng"].connected
    assert all(i.signup_url.startswith("https://") for i in overview.items)
    assert TavilyProvider.signup_url == rows["tavily"].signup_url
