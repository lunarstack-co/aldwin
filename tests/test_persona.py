"""Unit and integration tests for `aldwin.core.persona` and `SetPersonaTool`."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from fakes.model import FakeModel, FakeTurn

from aldwin.client import (
    AldwinClient,
    get_persona_summary,
    get_persona_text,
)
from aldwin.core.agent import build_agent
from aldwin.core.home import atomic_write, ensure_home
from aldwin.core.persona import (
    persona_summary,
    persona_text,
    read_default_persona,
    read_persona,
    write_persona,
)
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.tools import SetPersonaTool, build_persona_tool
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    Event,
    ToolResultEvent,
)


async def test_set_persona_approved_updates_file_and_next_model_call(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """TC-09: Approving `set_persona` updates `persona.md` and the next model call."""
    ensure_home(aldwin_home)
    new_persona = "Be ultra direct. Answer in one sentence whenever possible."

    fake_model.add_turns(
        FakeTurn.tool_call("set_persona", {"new_text": new_persona}),
        FakeTurn.text(["Updated my persona."]),
        FakeTurn.text(["Short answer."]),
    )

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda model_name, ask: build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        ),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events_turn_1: list[Event] = []
        async for ev in client.turn("Be more direct with me"):
            events_turn_1.append(ev)
            if isinstance(ev, ApprovalEvent):
                assert ev.summary == "Update persona?"
                assert ev.detail == new_persona
                approve_done = await client.approve(ev.approval_id, "yes")
                assert isinstance(approve_done, DoneEvent)

        tool_results = [e for e in events_turn_1 if isinstance(e, ToolResultEvent)]
        assert len(tool_results) == 1
        assert tool_results[0].ok is True
        assert tool_results[0].summary == "persona updated"

        persona_path = aldwin_home / "persona.md"
        assert (persona_path.stat().st_mode & 0o777) == 0o600
        assert persona_path.read_text(encoding="utf-8") == f"{new_persona}\n"

        # Step 2 of Turn 1 and Turn 2 both receive the updated persona in system prompt.
        assert f"## Persona\n{new_persona}" in fake_model.calls[1][0].content

        async for _ev in client.turn("What is 2 + 2?"):
            pass
        assert f"## Persona\n{new_persona}" in fake_model.calls[2][0].content
    finally:
        await client.close()
        await host.stop_host()


async def test_set_persona_declined_or_stopped_keeps_original_persona(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """TC-10: Declining or stopping `set_persona` leaves `persona.md` untouched."""
    ensure_home(aldwin_home)
    persona_path = aldwin_home / "persona.md"
    original_text = persona_path.read_text(encoding="utf-8")

    fake_model.add_turns(
        FakeTurn.tool_call("set_persona", {"new_text": "Speak like a pirate."}),
        FakeTurn.text(["Okay, keeping your current persona."]),
        FakeTurn.tool_call("set_persona", {"new_text": "Speak in rhymes."}),
    )

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda model_name, ask: build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        ),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()

        # Case 1: User declines ("no").
        turn_1_events: list[Event] = []
        async for ev in client.turn("Change persona"):
            turn_1_events.append(ev)
            if isinstance(ev, ApprovalEvent):
                await client.approve(ev.approval_id, "no")

        tool_results = [e for e in turn_1_events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) == 1
        assert tool_results[0].ok is False
        assert "denied by the user" in tool_results[0].summary.lower()
        assert persona_path.read_text(encoding="utf-8") == original_text

        # Case 2: User presses Esc (`stop`) while approval is pending.
        turn_2_events: list[Event] = []
        async for ev in client.turn("Change persona again"):
            turn_2_events.append(ev)
            if isinstance(ev, ApprovalEvent):
                await asyncio.sleep(0)
                await client.stop()

        done_events = [e for e in turn_2_events if isinstance(e, DoneEvent)]
        assert len(done_events) == 1
        assert done_events[0].reason == "stopped"
        assert persona_path.read_text(encoding="utf-8") == original_text
    finally:
        await client.close()
        await host.stop_host()


def test_read_persona_missing_fallback_and_over_cap_warning(
    aldwin_home: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TC-16: Missing `persona.md` uses default asset; over-cap warns once."""
    ensure_home(aldwin_home)
    persona_path = aldwin_home / "persona.md"

    # Missing file falls back to shipped default.
    persona_path.unlink()
    assert read_persona(aldwin_home, cap=2000) == read_default_persona()

    # Blank file also falls back to shipped default.
    atomic_write(persona_path, "   \n")
    assert read_persona(aldwin_home, cap=2000) == read_default_persona()

    # Over-cap file is truncated in prompt only and logged once.
    atomic_write(persona_path, "X" * 2500)
    with caplog.at_level(logging.WARNING, logger="aldwin.core.persona"):
        out_1 = read_persona(aldwin_home, cap=2000)
        out_2 = read_persona(aldwin_home, cap=2000)

    assert out_1 == "X" * 2000
    assert out_2 == "X" * 2000
    assert len(persona_path.read_text(encoding="utf-8")) == 2500

    warnings = [
        rec
        for rec in caplog.records
        if "persona.md" in rec.getMessage() and "2000" in rec.getMessage()
    ]
    assert len(warnings) == 1


async def test_set_persona_rejects_empty_or_over_2000_chars_without_asking(
    aldwin_home: Path,
) -> None:
    """TC-17: Invalid `new_text` returns error without calling `ask()` or writing."""
    ensure_home(aldwin_home)
    persona_path = aldwin_home / "persona.md"
    before = persona_path.read_text(encoding="utf-8")

    ask_calls: list[tuple[str, str]] = []

    async def _spy_ask(summary: str, detail: str = "") -> bool:
        ask_calls.append((summary, detail))
        return True

    tool: SetPersonaTool = build_persona_tool(_spy_ask, aldwin_home, cap=2000)

    for bad_input in ("", "   \n  ", "bad\x00char", "Z" * 2001):
        res = await tool.call({"new_text": bad_input})
        assert "Invalid persona" in res

    assert ask_calls == []
    assert persona_path.read_text(encoding="utf-8") == before

    with pytest.raises(ValueError, match="Invalid persona"):
        write_persona(aldwin_home, "")
    with pytest.raises(ValueError, match="Invalid persona"):
        write_persona(aldwin_home, "Y" * 2001)


def test_persona_text_and_summary_via_client(aldwin_home: Path) -> None:
    """TC-19: `persona_text` and `persona_summary` work via core and `aldwin.client`."""
    ensure_home(aldwin_home)
    write_persona(aldwin_home, "Concise and friendly assistant.")

    assert persona_text(aldwin_home) == "Concise and friendly assistant."
    assert get_persona_text(aldwin_home) == "Concise and friendly assistant."

    summary = persona_summary(aldwin_home, cap=2000)
    assert summary.path == (aldwin_home / "persona.md").resolve()
    assert summary.text == "Concise and friendly assistant."
    assert summary.size_chars == len("Concise and friendly assistant.")
    assert summary.cap_chars == 2000
    assert summary.truncated is False

    client_summary = get_persona_summary(aldwin_home)
    assert client_summary == summary


async def test_set_persona_approval_wait_exceeds_agent_tool_timeout(
    aldwin_home: Path,
) -> None:
    """TC-05: `SetPersonaTool` approval wait exceeding `Agent.tool_timeout` succeeds."""
    from agentling import Agent, ToolResultEvent

    ensure_home(aldwin_home)

    async def slow_approve(_s: str, _d: str = "") -> bool:
        await asyncio.sleep(0.15)
        return True

    persona_tool = build_persona_tool(slow_approve, aldwin_home, cap=2000)
    fake_model = FakeModel(
        turns=[
            FakeTurn.tool_call("set_persona", {"new_text": "Be brief."}),
            FakeTurn.text(["Updated."]),
        ]
    )
    agent = Agent(
        model=fake_model,
        tools=[persona_tool],
        tool_timeout=0.05,
    )
    session = agent.start()
    results = [
        ev.result
        async for ev in session.run("update persona", stream=True, reset=False)
        if isinstance(ev, ToolResultEvent)
    ]
    assert len(results) == 1
    assert results[0].is_error is False
    assert results[0].content == "persona updated"
    assert (aldwin_home / "persona.md").read_text(encoding="utf-8") == "Be brief.\n"
