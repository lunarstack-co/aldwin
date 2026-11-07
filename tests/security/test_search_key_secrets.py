"""A search key typed into /search lives only in secrets.yaml."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
from fakes.model import FakeModel

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.settings import AldwinConfig, SecretsConfig, load_secrets
from aldwin.host.in_process import InProcessTransport
from aldwin.host.runner import AldwinHost

CANARY_KEY = "ALDWIN_CANARY_7f3a-search-key"


class _Switch:
    def __init__(self) -> None:
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda request: (
            httpx.Response(200, json={})
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(lambda request: self.handler(request))


async def test_canary_key_never_leaves_secrets_file(
    aldwin_home: Path, fake_model: FakeModel
) -> None:
    switch = _Switch()
    """A connected key never shows up in events, logs, sessions or history."""
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
    sent: list[str] = []
    original = transport.send_event

    async def record(event: object) -> None:
        sent.append(event.model_dump_json())  # type: ignore[attr-defined]
        await original(event)  # type: ignore[arg-type]

    transport.send_event = record  # type: ignore[method-assign]
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        switch.handler = lambda request: httpx.Response(401, json={})
        await client.search_connect("brave", key=CANARY_KEY)
        switch.handler = lambda request: httpx.Response(200, json={})
        await client.search_connect("brave", key=CANARY_KEY)
        await client.search_list()
    finally:
        await client.close()
        await host.stop_host()

    assert len(sent) == 5
    assert all(CANARY_KEY not in line for line in sent)
    for path in aldwin_home.rglob("*"):
        if path.is_file() and path.name != "secrets.yaml":
            assert CANARY_KEY not in path.read_text(errors="ignore"), path
    assert load_secrets(aldwin_home).get_secret("brave") == CANARY_KEY
