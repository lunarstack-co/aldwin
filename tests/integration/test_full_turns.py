"""End-to-end integration tests for `AldwinClient` -> `AldwinHost` -> `aldwin.core`."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, cast

import httpx
import openai
from agentling.memory import ActionStep, FinalStep, TaskStep
from conftest import auto_approve, collect
from fakes.model import FakeModel, final, hang, raise_, text, tool_call

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, AskFn, InProcessTransport, SessionStore
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    ErrorEvent,
    Event,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)


async def test_plain_answer_streamed_in_five_deltas(
    host_client: AldwinClient,
    fake_model: FakeModel,
) -> None:
    """TC-03: `FakeModel` scripted text in 5 deltas yields 5 `TextEvent`s then done."""
    fake_model.add_turns(text("Hello world!", deltas=5))

    events = await collect(host_client.turn("Say hello"))
    text_deltas = [ev.delta for ev in events if isinstance(ev, TextEvent)]

    assert len(text_deltas) == 5
    assert "".join(text_deltas) == "Hello world!"
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "answered"


async def test_tool_call_approve_yes_executes_command(
    host_client: AldwinClient,
    fake_model: FakeModel,
) -> None:
    """TC-15: `run_command` + `approve(yes)` runs the command and completes the turn."""
    fake_model.add_turns(
        tool_call("run_command", {"command": "echo integration_approved"}),
        final("Command succeeded."),
    )

    events = await auto_approve(
        host_client,
        host_client.turn("run echo"),
        answer="yes",
    )

    assert any(
        isinstance(ev, ToolCallEvent) and ev.name == "run_command" for ev in events
    )
    assert any(isinstance(ev, ApprovalEvent) for ev in events)
    results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].summary == "exit 0"

    texts = [ev.delta for ev in events if isinstance(ev, TextEvent)]
    assert "".join(texts) == "Command succeeded."
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "answered"


async def test_tool_call_approve_no_skips_command_and_informs_model(
    host_client: AldwinClient,
    fake_model: FakeModel,
    tmp_path: Path,
) -> None:
    """TC-05: Declining approval skips `run_command` and sends `'user declined'`."""
    sentinel = tmp_path / "never_created.txt"
    fake_model.add_turns(
        tool_call("run_command", {"command": f"touch {sentinel}"}),
        final("Understood, I did not run that command."),
    )

    events = await auto_approve(
        host_client,
        host_client.turn("touch sentinel"),
        answer="no",
    )

    assert not sentinel.exists()
    results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
    assert len(results) == 1
    assert results[0].ok is False
    assert "denied by the user" in results[0].summary.lower()

    tool_msgs = [m for m in fake_model.received_messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "denied by the user" in (tool_msgs[0].content or "").lower()
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "answered"


async def test_stop_during_stream_emits_done_stopped_under_one_second(
    host_client: AldwinClient,
    fake_model: FakeModel,
) -> None:
    """TC-04: `Esc` (`stop`) during `hang()` emits `done(stopped)` in < 1 s."""
    hang_turn = hang(prefix_chunks=["Partial ", "answer"])
    fake_model.add_turns(hang_turn)

    events: list[Event] = []

    async def _consume() -> None:
        async for ev in host_client.turn("Tell a long story"):
            events.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.wait_for(hang_turn.hang_started.wait(), timeout=1.0)

    t0 = time.monotonic()
    stop_res = await host_client.stop()
    await asyncio.wait_for(task, timeout=1.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    assert isinstance(stop_res, DoneEvent) and stop_res.reason == "ok"
    assert [ev.delta for ev in events if isinstance(ev, TextEvent)] == [
        "Partial ",
        "answer",
    ]
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "stopped"

    saved = SessionStore().load_latest()
    assert isinstance(saved.memory.steps[-1], FinalStep)
    assert saved.memory.steps[-1].answer == "Partial answer"


async def test_stop_during_approval_counts_as_no(
    host_client: AldwinClient,
    fake_model: FakeModel,
    tmp_path: Path,
) -> None:
    """TC-16: `stop` during approval wait resolves as `no` and stops in < 1 s."""
    sentinel = tmp_path / "never_approved.txt"
    fake_model.add_turns(
        tool_call("run_command", {"command": f"touch {sentinel}"}),
    )

    events: list[Event] = []
    t0 = 0.0
    async for ev in host_client.turn("run touch"):
        events.append(ev)
        if isinstance(ev, ApprovalEvent):
            t0 = time.monotonic()
            stop_res = await host_client.stop()
            assert isinstance(stop_res, DoneEvent) and stop_res.reason == "ok"

    elapsed = time.monotonic() - t0
    assert elapsed < 1.0
    assert not sentinel.exists()
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "stopped"


async def test_model_error_emits_single_error_event(
    host_client: AldwinClient,
    fake_model: FakeModel,
) -> None:
    """TC-17: Unrecoverable model failure emits 1 `ErrorEvent` and 0 `DoneEvent`s."""
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    fake_model.add_turns(raise_(openai.APIConnectionError(request=cast(Any, req))))

    events = await collect(host_client.turn("Hello"))

    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].code == "model_unavailable"


async def test_max_steps_emits_done_max_steps(
    host_client: AldwinClient,
    fake_model: FakeModel,
) -> None:
    """TC-18: Reaching `max_steps=15` emits `DoneEvent(reason='max_steps')`."""
    for step in range(15):
        fake_model.add_turns(
            tool_call("recall", {"query": f"topic {step}"}, call_id=f"c_{step}")
        )
    fake_model.add_turns(final("Stopped at max steps."))

    events = await collect(host_client.turn("Run 15 recall steps"))

    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "max_steps"


async def test_crash_after_step_2_reloads_as_interrupted_and_resumes(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """TC-07: Crash after step 2 leaves `interrupted=True` and resumes cleanly."""
    hang_step3 = hang(prefix_chunks=["Mid-step"])
    fake_model.add_turns(
        tool_call("remember", {"text": "Fact step one"}, call_id="c1"),
        tool_call("remember", {"text": "Fact step two"}, call_id="c2"),
        hang_step3,
        final("Resumed and completed after crash."),
    )

    def builder(model_name: str, ask: AskFn) -> Any:
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        )

    store1 = SessionStore()
    transport1 = InProcessTransport()
    host1 = AldwinHost(transport1, session_store=store1, agent_builder=builder)
    await host1.start()
    client1 = AldwinClient(transport1.connect())
    await client1.connect()

    async def _run_until_crash() -> None:
        async for _ev in client1.turn("Remember two facts then crash"):
            pass

    turn_task = asyncio.create_task(_run_until_crash())
    await asyncio.wait_for(hang_step3.hang_started.wait(), timeout=1.0)

    # Simulate an abrupt terminal/process crash (SIGKILL) where no further disk writes
    # occur during teardown after Step 2 was persisted.
    store1.save = lambda *_a, **_kw: None  # type: ignore[method-assign]
    await client1.close()
    await host1.stop_host()
    turn_task.cancel()
    await asyncio.gather(turn_task, return_exceptions=True)

    # Reload from disk in a fresh host + client session.
    reloaded = SessionStore().load_latest()
    assert reloaded.meta.interrupted is True
    assert len(reloaded.memory.steps) == 3
    assert isinstance(reloaded.memory.steps[0], TaskStep)
    assert isinstance(reloaded.memory.steps[1], ActionStep)
    assert isinstance(reloaded.memory.steps[2], ActionStep)

    transport2 = InProcessTransport()
    host2 = AldwinHost(transport2, agent_builder=builder)
    await host2.start()
    client2 = AldwinClient(transport2.connect())
    try:
        await client2.connect()
        hist = await client2.history()
        assert hist.interrupted is True

        resume_events = await collect(client2.turn("continue"))
        assert isinstance(resume_events[-1], DoneEvent)
        assert resume_events[-1].reason == "answered"
        assert SessionStore().load_latest().meta.interrupted is False
    finally:
        await client2.close()
        await host2.stop_host()


async def test_new_conversation_starts_fresh_and_keeps_old_file(
    host_client: AldwinClient,
    fake_model: FakeModel,
    aldwin_home: Path,
) -> None:
    """TC-19: `/new` (`new_conversation()`) starts an empty session and keeps old."""
    fake_model.add_turns(final("First session answer."))
    await collect(host_client.turn("First session question"))

    before_hist = await host_client.history()
    assert len(before_hist.messages) == 2

    await host_client.new_conversation()
    after_hist = await host_client.history()
    assert after_hist.messages == []
    assert after_hist.interrupted is False

    session_files = list((aldwin_home / "sessions").glob("*.json"))
    assert len(session_files) == 2


async def test_fifty_turn_conversation_caps_context_at_20_user_turns(
    host_client: AldwinClient,
    fake_model: FakeModel,
) -> None:
    """TC-06: 50-turn conversation keeps <= 20 user turns and 1 terminal event each."""
    for i in range(50):
        if i % 10 == 0:
            fake_model.add_turns(
                tool_call(
                    "remember",
                    {"text": f"Milestone fact {i}"},
                    call_id=f"m_{i}",
                ),
                final(f"Recorded milestone {i}."),
            )
        else:
            fake_model.add_turns(final(f"Reply {i}"))

    t0 = time.monotonic()
    terminal_events: list[Event] = []
    for i in range(50):
        events = await collect(host_client.turn(f"User turn {i}"))
        terminals = [ev for ev in events if isinstance(ev, (DoneEvent, ErrorEvent))]
        assert len(terminals) == 1
        assert events[-1] == terminals[0]
        terminal_events.append(terminals[0])

    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"50 turns took {elapsed:.2f}s (budget < 5.0s)"
    assert len(terminal_events) == 50

    for call_idx, call_messages in enumerate(fake_model.calls):
        user_turns = sum(1 for msg in call_messages if msg.role == "user")
        assert user_turns <= 20, (
            f"Model call {call_idx} received {user_turns} user turns (max 20)"
        )


async def test_turn_with_tool_then_final_answer_emits_text_and_hides_final_answer_card(
    host_client: AldwinClient,
    fake_model: FakeModel,
) -> None:
    """TC-01: Turn finishing via `final_answer` renders Markdown and 1 tool."""
    from aldwin.cli.render import ChatRenderer
    from aldwin.cli.theme import create_console

    fake_model.add_turns(
        tool_call("recall", {"query": "metric"}, call_id="c_rec"),
        tool_call(
            "final_answer",
            {"answer": "You prefer **metric** units."},
            call_id="c_fa",
        ),
    )

    events = await collect(host_client.turn("What units?"))

    tool_calls = [ev for ev in events if isinstance(ev, ToolCallEvent)]
    tool_results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
    assert [tc.name for tc in tool_calls] == ["recall"]
    assert len(tool_results) == 1

    texts = [ev.delta for ev in events if isinstance(ev, TextEvent)]
    assert "".join(texts) == "You prefer **metric** units."
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "answered"

    console = create_console(plain=False, width=80, record=True)
    chat_renderer = ChatRenderer(console, plain=False)
    turn_renderer = chat_renderer.start_turn(clock=lambda: 100.0)
    try:
        for ev in events:
            if isinstance(ev, TextEvent):
                turn_renderer.on_text(ev.delta)
            elif isinstance(ev, ToolCallEvent):
                turn_renderer.on_tool_call(ev)
            elif isinstance(ev, ToolResultEvent):
                turn_renderer.on_tool_result(ev)
            elif isinstance(ev, DoneEvent):
                turn_renderer.finish_done(ev)
    finally:
        turn_renderer.close()

    rendered = console.export_text()
    assert "final_answer" not in rendered
    assert "You prefer metric units." in rendered
    assert "Worked for 0s · 1 tool" in rendered
