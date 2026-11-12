"""Host wiring for connected apps the user picks for one message."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from agentling.memory import TaskStep
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.connectors.picks import PickDrop
from aldwin.core.home import ensure_home
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import AppsUsedEvent, DoneEvent


def _host(home: Path, fake_model: FakeModel) -> tuple[AldwinHost, InProcessTransport]:
    def build(model_name: str, ask):  # noqa: ANN001
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=home,
            model=fake_model,
        )

    transport = InProcessTransport()
    return AldwinHost(transport, home=home, agent_builder=build), transport


def _task(host: AldwinHost) -> str:
    steps = [s for s in host._agent_session.memory.steps if isinstance(s, TaskStep)]
    return steps[0].task


async def test_an_app_that_is_not_connected_is_reported_and_the_task_is_plain(
    aldwin_home: Path,
) -> None:
    ensure_home(aldwin_home)
    fake_model = FakeModel()
    fake_model.add_turns(FakeTurn.text("ok"))
    host, transport = _host(aldwin_home, fake_model)
    await host.start()
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        events = [ev async for ev in client.turn("hi", apps=["linear"])]
        used = next(ev for ev in events if isinstance(ev, AppsUsedEvent))
        assert used.names == []
        assert [(d.name, d.reason) for d in used.dropped] == [("linear", "unknown")]
        assert isinstance(events[-1], DoneEvent)
        assert _task(host) == "hi"
        assert host._stored_session.turns is not None
        assert "apps" not in host._stored_session.turns[-1]
    finally:
        await client.close()
        await host.stop_host()


async def test_a_picked_app_is_named_to_the_model_and_kept_with_the_turn(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_home(aldwin_home)
    fake_model = FakeModel()
    fake_model.add_turns(FakeTurn.text("ok"))
    host, transport = _host(aldwin_home, fake_model)
    await host.start()

    def pick_apps(
        names: Sequence[str],
    ) -> tuple[list[tuple[str, str]], list[tuple[str, PickDrop]]]:
        assert list(names) == ["linear"]
        return [("linear", "Linear")], []

    monkeypatch.setattr(host._connectors, "pick_apps", pick_apps)
    client = AldwinClient(transport.connect())
    try:
        await client.connect()
        events = [
            ev async for ev in client.turn("Close the login bug", apps=["linear"])
        ]
        used = next(ev for ev in events if isinstance(ev, AppsUsedEvent))
        assert used.names == ["linear"]
        task = _task(host)
        assert task.startswith("<picked_apps>\n")
        assert "- Linear (connector: linear)" in task
        assert task.endswith("Close the login bug")
        assert host._stored_session.turns is not None
        assert host._stored_session.turns[-1]["apps"] == ["linear"]
        assert host._stored_session.turns[-1]["text"] == "Close the login bug"
    finally:
        await client.close()
        await host.stop_host()
