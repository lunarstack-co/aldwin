"""`/search`: list, connect, switch, update and disconnect search providers."""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable, Iterator

import pytest
from fakes.host import FakeClient, search_item

from aldwin.cli.commands import handle_slash_command
from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import create_console
from aldwin.protocol import DoneEvent, ErrorEvent, SearchProvidersEvent

ORDER = ("tavily", "searxng", "brave", "exa", "perplexity", "parallel", "firecrawl")


def _event(setting: str = "auto", **rows: dict[str, object]) -> SearchProvidersEvent:
    items = [search_item(name, **rows.get(name, {})) for name in ORDER]
    active = next((i.name for i in items if i.active), None)
    return SearchProvidersEvent(re="r", setting=setting, active=active, items=items)


def _tavily_connected() -> SearchProvidersEvent:
    return _event(
        tavily={
            "connected": True,
            "active": True,
            "source": "file",
            "detail": "tvly-…8f2a",
        },
        brave={"check_note": "key check uses one Brave query"},
    )


def _answers(*values: str) -> Callable[[str], Awaitable[str]]:
    it: Iterator[str] = iter(values)

    async def ask(_label: str) -> str:
        return next(it, "")

    return ask


async def _run(
    line: str,
    client: FakeClient,
    *,
    lines: tuple[str, ...] = (),
    secrets: tuple[str, ...] = (),
) -> str:
    console = create_console(plain=True, width=100, record=True, file=io.StringIO())
    await handle_slash_command(
        line,
        client=client,
        renderer=ChatRenderer(console, plain=True),
        prompt_line=_answers(*lines),
        prompt_secret=_answers(*secrets),
    )
    return console.export_text()


async def test_list_groups_connected_then_not_then_automatic() -> None:
    client = FakeClient()
    client.search_event = _tavily_connected()
    text = await _run("/search", client)

    assert "Web search · Tavily (automatic)" in text
    lines = [line.strip() for line in text.splitlines()]
    connected = lines.index("Connected")
    not_connected = lines.index("Not connected")
    assert connected < not_connected
    assert lines[connected + 1].startswith("1. Tavily (in use)")
    assert "key tvly-…8f2a · saved" in lines[connected + 1]
    assert lines[not_connected + 1].startswith("2. SearXNG")
    assert "key check uses one Brave query" in lines[not_connected + 2]
    assert lines[not_connected + 7].startswith("8. Automatic")
    assert "(current)" in lines[not_connected + 7]


async def test_connect_a_new_provider_checks_then_confirms() -> None:
    client = FakeClient()
    client.search_event = _tavily_connected()
    text = await _run("/search", client, lines=("3",), secrets=("brave-key-123",))

    assert "Get your Brave key at https://brave.example/keys" in text
    assert "Guide: https://askaldwin.com/docs/search/brave" in text
    assert "Note: the key check uses one Brave query." in text
    assert "Checking key…" in text
    assert "✓ Brave connected · now used for search" in text
    assert ("search.connect", "brave", "brave-key-123", None) in client.requests


async def test_rejected_key_explains_and_asks_again_then_cancel_saves_nothing() -> None:
    client = FakeClient()
    client.search_event = _tavily_connected()
    client.search_connect_results = [
        (
            ErrorEvent(
                re="r",
                code="auth_failed",
                message="Exa rejected this key (HTTP 401). Check it and try again.",
            ),
            [],
        )
    ]
    text = await _run("/search", client, lines=("4",), secrets=("bad-key", ""))

    assert "Exa rejected this key (HTTP 401). Check it and try again." in text
    assert "Cancelled." in text
    connects = [r for r in client.requests if r[0] == "search.connect"]
    assert connects == [("search.connect", "exa", "bad-key", None)]


async def test_connected_actions_use_update_disconnect() -> None:
    event = _event(
        tavily={"connected": True, "active": True, "source": "file", "detail": "…8f2a"},
        exa={"connected": True, "source": "file", "detail": "…1111"},
    )
    client = FakeClient()
    client.search_event = event
    # Exa is row 2; its actions are 1 Use, 2 Update key, 3 Disconnect.
    use_text = await _run("/search", client, lines=("2", "1"))
    assert ("search.use", "exa") in client.requests
    assert "✓ Search · Exa" in use_text

    update = FakeClient()
    update.search_event = event
    await _run("/search", update, lines=("2", "2"), secrets=("exa-new-key",))
    assert ("search.connect", "exa", "exa-new-key", None) in update.requests

    gone = FakeClient()
    gone.search_event = event
    text = await _run("/search", gone, lines=("2", "3", "y"))
    assert ("search.disconnect", "exa") in gone.requests
    assert "✓ Exa disconnected" in text

    kept = FakeClient()
    kept.search_event = event
    await _run("/search", kept, lines=("2", "3", ""))
    assert not any(r[0] == "search.disconnect" for r in kept.requests)


async def test_active_provider_has_no_use_action() -> None:
    client = FakeClient()
    client.search_event = _event(
        "tavily",
        tavily={"connected": True, "active": True, "source": "file", "detail": "…8f2a"},
    )
    text = await _run("/search", client, lines=("1",))
    assert "Use for search" not in text
    assert "1. Update key" in text


async def test_env_key_disconnect_shows_how_to_remove_it() -> None:
    message = (
        "Brave's key comes from BRAVE_API_KEY in your environment. Remove it from "
        "your shell profile, then restart Aldwin."
    )
    client = FakeClient()
    client.search_event = _event(
        brave={
            "connected": True,
            "active": True,
            "source": "env",
            "env_var": "BRAVE_API_KEY",
            "detail": "…77aa",
        }
    )
    client.search_disconnect_result = ErrorEvent(
        re="r", code="request_rejected", message=message
    )
    text = await _run("/search", client, lines=("1", "3", "y"))
    assert "key …77aa · from BRAVE_API_KEY" in text
    assert " ".join(message.split()) in " ".join(text.split())


async def test_searxng_address_default_and_detected_has_no_disconnect() -> None:
    client = FakeClient()
    client.search_event = _event()
    text = await _run("/search", client, lines=("2", ""))
    assert "Connected" not in text.replace("Not connected", "")
    assert (
        "search.connect",
        "searxng",
        None,
        "http://127.0.0.1:8080",
    ) in client.requests

    detected = FakeClient()
    detected.search_event = _event(
        searxng={
            "connected": True,
            "active": True,
            "source": "detected",
            "detail": "http://localhost:8080",
        }
    )
    text = await _run("/search", detected, lines=("1",))
    assert "Change address" in text
    assert "Disconnect" not in text
    assert "http://localhost:8080 · found automatically" in text


async def test_direct_names_connect_or_switch() -> None:
    client = FakeClient()
    client.search_event = _tavily_connected()
    switched = await _run("/search tavily", client)
    assert ("search.use", "tavily") in client.requests
    assert "✓ Search · Tavily" in switched

    new = FakeClient()
    new.search_event = _tavily_connected()
    text = await _run("/search brave", new, secrets=("brave-key-xyz",))
    assert "API key" not in text or "Get your Brave key" in text
    assert ("search.connect", "brave", "brave-key-xyz", None) in new.requests

    unknown = FakeClient()
    unknown.search_event = _tavily_connected()
    text = await _run("/search bing", unknown)
    assert "Unknown search provider 'bing'." in text
    assert "Usage: /search [auto, tavily, searxng, brave" in text


async def test_automatic_row_and_shortcut() -> None:
    client = FakeClient()
    client.search_event = _tavily_connected()
    text = await _run("/search", client, lines=("8",))
    assert ("search.use", "auto") in client.requests
    assert "✓ Search · automatic (using Tavily)" in text

    nothing = FakeClient()
    nothing.search_event = _event()
    text = await _run("/search auto", nothing)
    assert "✓ Search · automatic (nothing connected yet)" in text


async def test_enter_cancels_and_bad_number_is_explained() -> None:
    client = FakeClient()
    client.search_event = _tavily_connected()
    await _run("/search", client, lines=("",))
    assert [r[0] for r in client.requests] == ["search.list"]

    text = await _run("/search", client, lines=("42",))
    assert "Invalid selection '42'. Enter a number from 1 to 8." in text


async def test_status_prints_text_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    monkeypatch.setattr(client, "describe_active_search", lambda: "Search · SearXNG")
    text = await _run("/search status", client)
    assert "Search · SearXNG" in text
    assert not any(r[0] == "search.list" for r in client.requests)


async def test_connect_notice_is_shown() -> None:
    client = FakeClient()
    client.search_event = _tavily_connected()
    client.search_connect_results = [
        (
            DoneEvent(re="r", reason="ok"),
            ["BRAVE_API_KEY in your environment still takes priority over this key."],
        )
    ]
    text = await _run("/search", client, lines=("3",), secrets=("k-123456",))
    assert "BRAVE_API_KEY in your environment still takes priority" in text


async def test_host_failure_is_shown_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the host cannot list providers, /search prints why and returns."""
    from aldwin.client import ProtocolError

    client = FakeClient()

    async def broken() -> SearchProvidersEvent:
        raise ProtocolError(
            "Invalid configuration in secrets.yaml: fix the value.",
            code="invalid_request",
        )

    monkeypatch.setattr(client, "search_list", broken)
    text = await _run("/search", client)
    assert "Invalid configuration in secrets.yaml: fix the value." in text
    text = await _run("/search brave", client)
    assert "Invalid configuration in secrets.yaml" in text


async def test_bad_number_asks_again() -> None:
    """In plain mode a bad number keeps the list open instead of closing it."""
    client = FakeClient()
    client.search_event = _tavily_connected()
    await _run("/search", client, lines=("42", "8"))
    assert ("search.use", "auto") in client.requests
