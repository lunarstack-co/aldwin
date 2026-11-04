"""Integration tests for `AldwinClient` -> `AldwinHost` -> `aldwin.core`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agentling import (
    ModelConnectionError,
    ModelRateLimitError,
    ModelServerError,
    RetryPolicy,
)
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.home import ensure_home
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.protocol import DoneEvent, ErrorEvent, Event, StatusEvent, TextEvent


async def _collect(client: AldwinClient, text: str) -> list[Event]:
    events: list[Event] = []
    async for ev in client.turn(text):
        events.append(ev)
    return events


async def test_host_emits_retry_status_events_then_answers(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two 429 errors emit two `StatusEvent`s before the streamed answer."""
    ensure_home(aldwin_home)
    err_429 = ModelRateLimitError("Rate limit reached")
    fake_model = FakeModel(
        turns=[
            FakeTurn.error(err_429),
            FakeTurn.error(err_429),
            FakeTurn.text(["Recovered answer."]),
        ]
    )

    from agentling import AgentSession

    async def fast_sleep(_self: Any, _delay: float) -> None:
        return None

    monkeypatch.setattr(AgentSession, "_sleep_unless_interrupted", fast_sleep)

    def builder(model_name: str, ask: AskFn) -> Any:
        agent = build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        )
        agent.retry = RetryPolicy(
            max_retries=3,
            base_delay=1.0,
            jitter=0.0,
        )
        return agent

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder)
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect(client, "Hello")

        statuses = [ev.message for ev in events if isinstance(ev, StatusEvent)]
        assert statuses == [
            "OpenAI is busy. Retrying in 1s (1/3)…",
            "OpenAI is busy. Retrying in 2s (2/3)…",
        ]
        texts = [ev.delta for ev in events if isinstance(ev, TextEvent)]
        assert texts == ["Recovered answer."]
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"
    finally:
        await client.close()
        await host.stop_host()


async def test_host_emits_retry_statuses_on_connection_errors(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection failures emit 'No internet' status lines then model_unavailable."""
    ensure_home(aldwin_home)
    err_conn = ModelConnectionError("connection dropped")
    fake_model = FakeModel(
        turns=[
            FakeTurn.error(err_conn),
            FakeTurn.error(err_conn),
            FakeTurn.error(err_conn),
            FakeTurn.error(err_conn),
        ]
    )

    from agentling import AgentSession

    async def fast_sleep(_self: Any, _delay: float) -> None:
        return None

    monkeypatch.setattr(AgentSession, "_sleep_unless_interrupted", fast_sleep)

    def builder(model_name: str, ask: AskFn) -> Any:
        agent = build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        )
        agent.retry = RetryPolicy(
            max_retries=3,
            base_delay=1.0,
            jitter=0.0,
        )
        return agent

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder)
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect(client, "Hello")

        statuses = [ev.message for ev in events if isinstance(ev, StatusEvent)]
        assert statuses == [
            "No internet connection. Retrying in 1s (1/3)…",
            "No internet connection. Retrying in 2s (2/3)…",
            "No internet connection. Retrying in 4s (3/3)…",
        ]
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].code == "model_unavailable"
    finally:
        await client.close()
        await host.stop_host()


async def test_host_emits_3_retry_statuses_then_rate_limited_on_503(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three failed retries on 503 emit busy status then rate_limited."""
    ensure_home(aldwin_home)
    err_503 = ModelServerError("Service unavailable")
    fake_model = FakeModel(
        turns=[
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
        ]
    )

    from agentling import AgentSession

    async def fast_sleep(_self: Any, _delay: float) -> None:
        return None

    monkeypatch.setattr(AgentSession, "_sleep_unless_interrupted", fast_sleep)

    def builder(model_name: str, ask: AskFn) -> Any:
        agent = build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        )
        agent.retry = RetryPolicy(
            max_retries=3,
            base_delay=1.0,
            jitter=0.0,
        )
        return agent

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder)
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect(client, "Hello")

        statuses = [ev.message for ev in events if isinstance(ev, StatusEvent)]
        assert statuses == [
            "OpenAI is busy. Retrying in 1s (1/3)…",
            "OpenAI is busy. Retrying in 2s (2/3)…",
            "OpenAI is busy. Retrying in 4s (3/3)…",
        ]
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].code == "rate_limited"
        assert "busy" in (events[-1].message or "").lower()
    finally:
        await client.close()
        await host.stop_host()


async def test_host_core_context_and_model_switch_end_to_end(
    aldwin_home: Path,
) -> None:
    """End-to-end `AldwinHost` + `build_agent` switches model cleanly."""
    ensure_home(aldwin_home)
    models_by_name: dict[str, FakeModel] = {
        "gpt-4o": FakeModel(turns=[FakeTurn.text(["Hello Fola from 4o"])]),
        "gpt-4.1": FakeModel(turns=[FakeTurn.text(["Hello Fola from 4.1"])]),
    }

    def builder(model_name: str, ask: AskFn) -> Any:
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=models_by_name[model_name],
        )

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=builder,
        initial_model="gpt-4o",
        available_models=["gpt-4o", "gpt-4.1"],
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        await _collect(client, "My name is Fola")

        res = await client.set_model("gpt-4.1")
        assert isinstance(res, DoneEvent) and res.reason == "ok"

        (aldwin_home / "persona.md").write_text("Be concise.\n", encoding="utf-8")
        await _collect(client, "What is my name?")

        sent = models_by_name["gpt-4.1"].received_messages
        assert sent[0].role == "system"
        assert "## Persona\nBe concise." in sent[0].content
        assert sent[0].content.count("Available skills:") == 1
        assert [(m.role, m.content) for m in sent[1:]] == [
            ("user", "My name is Fola"),
            ("assistant", "Hello Fola from 4o"),
            ("user", "What is my name?"),
        ]
    finally:
        await client.close()
        await host.stop_host()
