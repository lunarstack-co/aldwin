"""The host picks up config.yaml and secrets.yaml changes made by another process."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from agentling import Agent
from fakes.model import FakeModel, FakeTurn
from pydantic import SecretStr

from aldwin.client import AldwinClient
from aldwin.core.home import ensure_home
from aldwin.core.settings import (
    SecretsConfig,
    save_secrets,
    update_config_limits,
    update_config_model,
)
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.protocol import DoneEvent, ErrorEvent, Event, NoticeEvent, StatusEvent


@dataclass
class Rig:
    host: AldwinHost
    client: AldwinClient
    home: Path
    model: FakeModel
    builds: list[str]


@pytest.fixture
async def rig(aldwin_home: Path, fake_model: FakeModel) -> AsyncIterator[Rig]:
    ensure_home(aldwin_home)
    builds: list[str] = []

    def builder(model_name: str, _ask: AskFn) -> Agent:
        builds.append(model_name)
        return Agent(model=fake_model)

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder, home=aldwin_home)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        yield Rig(host, client, aldwin_home, fake_model, builds)
    finally:
        await client.close()
        await host.stop_host()


async def _turn(rig: Rig, text: str = "hi") -> list[Event]:
    rig.model.add_turns(FakeTurn.text(["ok"]))
    return [event async for event in rig.client.turn(text)]


async def test_config_fingerprint_change_reloads_before_turn(rig: Rig) -> None:
    """TC-03: a limits change on disk rebuilds the agent before the next turn."""
    await _turn(rig)
    builds_before = len(rig.builds)

    update_config_limits({"max_steps": 42}, rig.home)
    events = await _turn(rig)

    assert len(rig.builds) == builds_before + 1
    assert rig.host._config.limits.max_steps == 42
    assert isinstance(events[-1], DoneEvent) and events[-1].reason == "answered"


async def test_unchanged_fingerprint_skips_rebuild(rig: Rig) -> None:
    """TC-17: with nothing changed on disk, turns reuse the same agent."""
    builds_before = len(rig.builds)
    agent = rig.host._agent

    await _turn(rig)
    await _turn(rig)

    assert len(rig.builds) == builds_before
    assert rig.host._agent is agent


async def test_reload_keeps_session_model(rig: Rig) -> None:
    """TC-18: another door changing the default model leaves this chat's model."""
    active = rig.host.model
    update_config_model("gpt-4o-mini", rig.home)

    await _turn(rig)

    assert rig.host.model == active
    assert rig.builds[-1] == active


async def test_reload_emits_no_status_or_notice(rig: Rig) -> None:
    """TC-19: reloading is silent; the turn shows only its own output."""
    update_config_limits({"notes_days": 3}, rig.home)

    events = await _turn(rig)

    assert not any(isinstance(e, StatusEvent | NoticeEvent) for e in events)


async def test_set_model_refreshes_fingerprint_no_spurious_rebuild(rig: Rig) -> None:
    """TC-20: a model switch that writes config.yaml does not rebuild again."""
    done = await rig.client.set_model("openai/gpt-4o-mini")
    assert isinstance(done, DoneEvent)
    builds_after_switch = len(rig.builds)

    await _turn(rig)

    assert len(rig.builds) == builds_after_switch


async def test_secrets_only_change_triggers_reload(rig: Rig) -> None:
    """TC-21: a key saved by another door is loaded before the next turn."""
    builds_before = len(rig.builds)
    save_secrets(SecretsConfig(openai_api_key=SecretStr("sk-from-web")), rig.home)

    await _turn(rig)

    assert len(rig.builds) == builds_before + 1
    key = rig.host._secrets.openai_api_key
    assert key is not None and key.get_secret_value() == "sk-from-web"


async def test_corrupt_config_on_reload_fails_turn(rig: Rig) -> None:
    """TC-22: a broken config.yaml fails the turn with a fixable message."""
    config_path = rig.home / "config.yaml"
    good = config_path.read_text(encoding="utf-8")
    config_path.write_text("limits:\n  max_steps: lots\n", encoding="utf-8")

    events = await _turn(rig)

    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "invalid_request"
    assert "config.yaml" in events[-1].message
    assert "limits.max_steps" in events[-1].message
    assert rig.host._active_turn_id is None

    config_path.write_text(good, encoding="utf-8")
    rig.model.clear_turns()
    events = await _turn(rig)
    assert isinstance(events[-1], DoneEvent)


async def test_mid_turn_disk_change_applies_on_next_turn(rig: Rig) -> None:
    """TC-23: a change during a turn waits for the next turn."""
    hang = FakeTurn.hang(prefix_chunks=["Working"])
    rig.model.add_turns(hang)
    running = asyncio.create_task(_drain(rig.client.turn("long job")))
    await asyncio.wait_for(hang.hang_started.wait(), timeout=2.0)
    builds_during = len(rig.builds)

    update_config_limits({"max_steps": 7}, rig.home)
    await asyncio.sleep(0.05)
    assert len(rig.builds) == builds_during

    await rig.client.stop()
    await asyncio.wait_for(running, timeout=2.0)
    await _turn(rig)

    assert len(rig.builds) == builds_during + 1
    assert rig.host._config.limits.max_steps == 7


async def _drain(events: AsyncIterator[Event]) -> None:
    async for _event in events:
        pass
