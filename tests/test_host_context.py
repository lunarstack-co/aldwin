"""Unit and integration tests for `context_usage` and `DoneEvent.context`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentling import Usage
from conftest import collect
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.host.display import context_usage
from aldwin.protocol import DoneEvent


def test_context_breakdown_maths_with_tool_definitions_remainder() -> None:
    """Segment estimates divide chars by 3.5 and put the remainder in `tools`."""
    chars = {
        "instructions": 3500,
        "skills": 700,
        "persona": 350,
        "memory": 0,
        "environment": 175,
        "connectors": 0,
        "conversation": 7000,
    }
    usage = context_usage(chars, used=5000, limit=128000)
    assert usage.limit == 128000
    assert usage.used == 5000

    by_key = {seg.key: seg.tokens for seg in usage.segments}
    assert by_key == {
        "instructions": 1000,
        "skills": 200,
        "persona": 100,
        "memory": 0,
        "environment": 50,
        "connectors": 0,
        "conversation": 2000,
        "tools": 1650,
    }
    assert sum(by_key.values()) == 5000


def test_context_breakdown_scales_down_when_estimates_exceed_used() -> None:
    """When char estimates exceed `used`, segments scale to sum to `used`."""
    chars = {
        "instructions": 3500,
        "skills": 700,
        "persona": 350,
        "memory": 0,
        "environment": 175,
        "connectors": 0,
        "conversation": 7000,
    }
    usage = context_usage(chars, used=2000, limit=128000)
    assert usage.used == 2000
    by_key = {seg.key: seg.tokens for seg in usage.segments}
    assert by_key["tools"] == 0
    assert all(val >= 0 for val in by_key.values())
    assert sum(by_key.values()) == 2000


async def _run_with_window(
    fake_model: FakeModel,
    aldwin_home: Path,
    *,
    window: int | None,
) -> DoneEvent:
    setattr(fake_model, "context_window", window)

    def builder(model_name: str, ask: AskFn) -> Any:
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        )

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder, home=aldwin_home)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        events = await collect(client.turn("hello"))
    finally:
        await client.close()
        await host.stop_host()

    return next(e for e in events if isinstance(e, DoneEvent))


async def test_multi_step_turn_uses_last_step_input_tokens(
    fake_model: FakeModel,
    aldwin_home: Path,
) -> None:
    """A 2-step turn reports the second step's input tokens, not the sum."""
    fake_model.add_turns(
        FakeTurn.tool_call(
            "calculate",
            {"expression": "2 + 2"},
            call_id="c1",
            usage=Usage(input_tokens=3000, output_tokens=20),
        ),
        FakeTurn.final(
            "Four.",
            usage=Usage(input_tokens=4200, output_tokens=10),
        ),
    )

    done = await _run_with_window(fake_model, aldwin_home, window=128000)
    assert done.usage is not None
    assert done.usage.input_tokens == 7200
    assert done.context is not None
    assert done.context.used == 4200
    assert done.context.limit == 128000
    assert sum(s.tokens for s in done.context.segments) == 4200


async def test_limit_comes_from_model_context_window(
    fake_model: FakeModel,
    aldwin_home: Path,
) -> None:
    """Single-step turn with `context_window = 128000` sets `limit == 128000`."""
    fake_model.add_turns(
        FakeTurn.final("Hi!", usage=Usage(input_tokens=5100, output_tokens=12))
    )
    done = await _run_with_window(fake_model, aldwin_home, window=128000)
    assert done.context is not None
    assert done.context.limit == 128000
    assert done.context.used == 5100
    assert sum(s.tokens for s in done.context.segments) == 5100


async def test_unknown_context_window_omits_done_context(
    fake_model: FakeModel,
    aldwin_home: Path,
) -> None:
    """When `context_window` is `None`, `DoneEvent.context` is omitted."""
    fake_model.add_turns(
        FakeTurn.final("Hi!", usage=Usage(input_tokens=5100, output_tokens=12))
    )
    done = await _run_with_window(fake_model, aldwin_home, window=None)
    assert done.context is None
