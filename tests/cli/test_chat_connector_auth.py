"""The chat screen answers `connector.auth` when a connector Aldwin proposed
needs the user to sign in during a turn."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fakes.host import FakeClient
from prompt_toolkit.input.defaults import create_pipe_input

from aldwin.cli import connector_auth
from aldwin.cli.chat_screen import run_chat
from aldwin.cli.theme import create_console
from aldwin.client import bootstrap
from aldwin.protocol import (
    ConnectorAuthEvent,
    DoneEvent,
    Event,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)

TOKEN = "tok-hidden-7c21"


def _turn(auth: ConnectorAuthEvent) -> list[Event]:
    return [
        ToolCallEvent(
            re="r1", call_id="c1", name="propose_connector", args_preview="gated"
        ),
        auth,
        ToolResultEvent(
            re="r1", call_id="c1", ok=True, summary="connected", duration_ms=5
        ),
        TextEvent(re="r1", delta="Connected and ready."),
        DoneEvent(re="r1", reason="answered"),
    ]


async def _chat(client: FakeClient, feed: Any, aldwin_home: Path) -> str:
    _ = aldwin_home
    boot = bootstrap(plain=False)
    buf = io.StringIO()
    console = create_console(plain=False, width=100, record=True, file=buf)
    with create_pipe_input() as pipe:
        pipe.send_text("connect gated\r")
        feeder = asyncio.create_task(feed(pipe))
        with patch(
            "aldwin.client.verify_active_openai_key", new=AsyncMock(return_value="ok")
        ):
            rc = await asyncio.wait_for(
                run_chat(boot, client=client, console=console, input_dev=pipe), 10
            )
        await feeder
    assert rc == 0
    return console.export_text()


async def test_token_prompt_is_hidden_and_the_turn_carries_on(
    aldwin_home: Path,
) -> None:
    client = FakeClient()
    client.queue_turn(
        _turn(
            ConnectorAuthEvent(
                re="r1",
                name="gated",
                title="Gated",
                mode="token",
                token_help_url="https://gated.example/tokens",
                expires_in=300,
            )
        )
    )

    async def feed(pipe: Any) -> None:
        await client.auth_emitted_event.wait()
        await asyncio.sleep(0.2)
        pipe.send_text(f"{TOKEN}\r")
        await asyncio.sleep(0.2)
        pipe.send_text("/exit\r")

    text = await _chat(client, feed, aldwin_home)

    assert ("auth_token", "gated", len(TOKEN)) in client.requests
    assert "Gated needs a token." in text
    assert "Create one at https://gated.example/tokens" in text
    assert "Connected and ready." in text
    assert TOKEN not in text


async def test_browser_sign_in_closes_its_prompt_when_the_flow_moves_on(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(connector_auth.webbrowser, "open", fake_open)
    client = FakeClient()
    client.queue_turn(
        _turn(
            ConnectorAuthEvent(
                re="r1",
                name="gated",
                title="Gated",
                mode="browser",
                url="https://gated.example/authorize",
                expires_in=300,
            )
        )
    )

    async def feed(pipe: Any) -> None:
        await client.auth_emitted_event.wait()
        for _ in range(100):
            if opened:
                break
            await asyncio.sleep(0.02)
        client.release_auth()
        await asyncio.sleep(0.2)
        pipe.send_text("/exit\r")

    text = await _chat(client, feed, aldwin_home)

    assert opened == ["https://gated.example/authorize"]
    assert "Sign in to Gated" in text
    assert "Connected and ready." in text
    assert ("stop",) not in client.requests
