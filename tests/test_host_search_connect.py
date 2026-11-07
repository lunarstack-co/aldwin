"""Connecting search providers and removing model keys over the protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fakes.model import FakeModel

from aldwin.client import AldwinClient, ProtocolError
from aldwin.core.agent import build_agent
from aldwin.core.settings import (
    AldwinConfig,
    SecretsConfig,
    load_config,
    load_secrets,
    save_secrets,
)
from aldwin.core.tools.web_search import searxng as searxng_mod
from aldwin.host.in_process import InProcessTransport
from aldwin.host.runner import AldwinHost
from aldwin.protocol import (
    DoneEvent,
    ErrorEvent,
    ProviderDisconnectRequest,
    SearchConnectRequest,
    SearchDisconnectRequest,
    SearchListRequest,
    SearchProviderItem,
    SearchProvidersEvent,
    SearchUseRequest,
)
from aldwin.protocol.messages import parse_message

CANARY_KEY = "ALDWIN_CANARY_7f3a-search-key"

Handler = Callable[[httpx.Request], httpx.Response]


class _Switch:
    """A mock transport whose answer each test can change."""

    def __init__(self) -> None:
        self.handler: Handler = lambda request: httpx.Response(200, json={})
        self.seen: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        def route(request: httpx.Request) -> httpx.Response:
            self.seen.append(request)
            return self.handler(request)

        return httpx.MockTransport(route)


@pytest.fixture
def switch() -> _Switch:
    return _Switch()


@pytest.fixture(autouse=True)
def _no_searxng(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SEARXNG_URL", "SEARXNG_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(searxng_mod, "_LOCAL_SEARXNG_CANDIDATES", ())
    searxng_mod.clear_searxng_detection_cache()


@pytest.fixture
async def client(
    aldwin_home: Path, fake_model: FakeModel, switch: _Switch
) -> AsyncIterator[AldwinClient]:
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        home=aldwin_home,
        agent_builder=lambda m, ask: build_agent(
            AldwinConfig(model=m),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        ),
        backend_transport=switch.transport(),
    )
    await host.start()
    connected = AldwinClient(transport.connect())
    await connected.connect()
    try:
        yield connected
    finally:
        await connected.close()
        await host.stop_host()


def test_search_messages_round_trip_and_hide_keys() -> None:
    """Every new message survives JSON; a key never shows in repr()."""
    connect = SearchConnectRequest(provider="brave", key=CANARY_KEY)
    assert CANARY_KEY not in repr(connect)
    for message in (
        SearchListRequest(),
        connect,
        SearchConnectRequest(provider="searxng", url="http://127.0.0.1:8080"),
        SearchUseRequest(provider="auto"),
        SearchDisconnectRequest(provider="exa"),
        ProviderDisconnectRequest(provider="gemini"),
        SearchProvidersEvent(
            re="r1",
            setting="auto",
            active="tavily",
            items=[
                SearchProviderItem(
                    name="tavily", title="Tavily", kind="hosted", connected=True
                )
            ],
        ),
    ):
        assert parse_message(message.model_dump_json()) == message


async def test_search_list_reports_providers(
    client: AldwinClient, aldwin_home: Path
) -> None:
    save_secrets(
        SecretsConfig.model_validate({"exa_api_key": "exa-key-12345678"}), aldwin_home
    )
    event = await client.search_list()
    assert event.setting == "auto"
    assert event.active == "exa"
    exa = next(i for i in event.items if i.name == "exa")
    assert exa.connected and exa.source == "file"
    assert "exa-key-12345678" not in event.model_dump_json()


async def test_connect_ok_saves_and_uses(
    client: AldwinClient, aldwin_home: Path, switch: _Switch
) -> None:
    result, notices = await client.search_connect("tavily", key="tvly-good-key-1234")
    assert isinstance(result, DoneEvent)
    assert notices == []
    assert switch.seen[0].url.path == "/usage"
    assert load_secrets(aldwin_home).get_secret("tavily") == "tvly-good-key-1234"
    assert load_config(aldwin_home).search.provider == "tavily"


async def test_connect_ok_with_env_override_sends_notice(
    client: AldwinClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXA_API_KEY", "exa-env-key-0000")
    result, notices = await client.search_connect("exa", key="exa-other-key-1111")
    assert isinstance(result, DoneEvent)
    assert notices == [
        "EXA_API_KEY in your environment still takes priority over this key."
    ]


@pytest.mark.parametrize(
    ("provider", "status", "code", "message"),
    [
        (
            "brave",
            401,
            "auth_failed",
            "Brave rejected this key (HTTP 401). Check it and try again.",
        ),
        (
            "tavily",
            402,
            "request_rejected",
            "Tavily accepted the key, but the account is out of credits (HTTP 402). "
            "Add credit, then connect again.",
        ),
        (
            "tavily",
            429,
            "rate_limited",
            "Tavily is rate limiting this key (HTTP 429). Wait a minute and try again.",
        ),
        (
            "exa",
            503,
            "search_unavailable",
            "Could not reach Exa to check the key (HTTP 503). Check your connection "
            "and try again.",
        ),
        (
            "perplexity",
            418,
            "search_unavailable",
            "Perplexity gave an unexpected answer while checking the key (HTTP 418). "
            "Try again in a minute.",
        ),
    ],
)
async def test_connect_failures_explain_and_save_nothing(
    client: AldwinClient,
    aldwin_home: Path,
    switch: _Switch,
    provider: str,
    status: int,
    code: str,
    message: str,
) -> None:
    switch.handler = lambda request: httpx.Response(status, json={})
    result, _ = await client.search_connect(provider, key="some-key-12345678")
    assert isinstance(result, ErrorEvent)
    assert result.code == code
    assert result.message == message
    assert load_secrets(aldwin_home).get_secret(provider) is None


async def test_connect_timeout_is_explained(
    client: AldwinClient, switch: _Switch
) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    switch.handler = timeout
    result, _ = await client.search_connect("firecrawl", key="fc-key-12345678")
    assert isinstance(result, ErrorEvent)
    assert result.message == (
        "Could not reach Firecrawl to check the key (request timed out). "
        "Check your connection and try again."
    )


async def test_connect_needs_a_key_or_known_provider(client: AldwinClient) -> None:
    empty, _ = await client.search_connect("brave", key="  ")
    assert isinstance(empty, ErrorEvent)
    assert empty.message == "Enter a Brave API key."
    unknown, _ = await client.search_connect("bing", key="k")
    assert isinstance(unknown, ErrorEvent)
    assert unknown.code == "invalid_request"


async def test_connect_searxng(
    client: AldwinClient, aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", lambda *_a, **_k: False)
    dead, _ = await client.search_connect("searxng", url="http://127.0.0.1:9")
    assert isinstance(dead, ErrorEvent)
    assert dead.message == (
        "Could not reach SearXNG (no SearXNG with JSON at http://127.0.0.1:9). "
        "Start it with JSON output on, or check the address."
    )
    bad, _ = await client.search_connect("searxng", url="box:8080")
    assert isinstance(bad, ErrorEvent)
    assert bad.message == "Enter an address starting with http:// or https://."

    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", lambda *_a, **_k: True)
    ok, _ = await client.search_connect("searxng", url="http://127.0.0.1:8888")
    assert isinstance(ok, DoneEvent)
    assert load_config(aldwin_home).search.searxng_url == "http://127.0.0.1:8888"


async def test_use_and_disconnect(
    client: AldwinClient, aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_secrets(
        SecretsConfig.model_validate({"brave_api_key": "brave-key-12345678"}),
        aldwin_home,
    )
    used = await client.search_use("brave")
    assert isinstance(used, DoneEvent)
    assert load_config(aldwin_home).search.provider == "brave"

    unready = await client.search_use("exa")
    assert isinstance(unready, ErrorEvent)
    assert unready.code == "request_rejected"

    gone = await client.search_disconnect("brave")
    assert isinstance(gone, DoneEvent)
    assert load_config(aldwin_home).search.provider == "auto"

    again = await client.search_disconnect("brave")
    assert isinstance(again, ErrorEvent)
    assert again.message == "Brave is not connected."

    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-env")
    env = await client.search_disconnect("parallel")
    assert isinstance(env, ErrorEvent)
    assert env.message == (
        "Parallel's key comes from PARALLEL_API_KEY in your environment. Remove it "
        "from your shell profile, then restart Aldwin."
    )


async def test_disconnect_model_provider(
    client: AldwinClient, aldwin_home: Path
) -> None:
    save_secrets(
        SecretsConfig.model_validate(
            {"gemini_api_key": "AIza-key", "openai_api_key": "sk-openai"}
        ),
        aldwin_home,
    )
    removed = await client.disconnect_provider("gemini")
    assert isinstance(removed, DoneEvent)
    assert load_secrets(aldwin_home).get_secret("gemini_api_key") is None

    in_use = await client.disconnect_provider("openai")
    assert isinstance(in_use, ErrorEvent)
    assert (
        in_use.message
        == "OpenAI runs the current model. Switch to another model first."
    )
    assert load_secrets(aldwin_home).get_secret("openai_api_key") == "sk-openai"


async def test_broken_secrets_file_is_explained_not_crashed(
    client: AldwinClient, aldwin_home: Path
) -> None:
    """A secrets.yaml broken mid-session gives one clear error, not a crash."""
    secrets_path = aldwin_home / "secrets.yaml"
    secrets_path.write_text("tavily_api_key: [not, a, string]\n")
    secrets_path.chmod(0o600)

    with pytest.raises(ProtocolError) as exc_info:
        await client.search_list()
    assert exc_info.value.code == "invalid_request"
    assert "secrets.yaml" in str(exc_info.value)

    disconnect = await client.search_disconnect("tavily")
    assert isinstance(disconnect, ErrorEvent)
    assert "secrets.yaml" in disconnect.message


async def test_env_disconnect_says_saved_key_was_removed(
    client: AldwinClient, aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_secrets(
        SecretsConfig.model_validate({"exa_api_key": "exa-file-key"}), aldwin_home
    )
    monkeypatch.setenv("EXA_API_KEY", "exa-env-key")
    result = await client.search_disconnect("exa")
    assert isinstance(result, ErrorEvent)
    assert result.message.startswith(
        "The saved key was removed. Exa's key comes from EXA_API_KEY"
    )
    assert load_secrets(aldwin_home).get_secret("exa_api_key") is None
