"""Tests for `aldwin.host.runner` (`RequestScope`, `ApprovalBroker`, `AldwinHost`)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Literal, cast

import httpx
import openai
import pytest
from agentling import Agent, Tool, tool
from agentling.errors import ModelError
from agentling.memory import FinalStep
from agentling.models import Usage
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient, ProtocolError
from aldwin.core.home import ensure_home
from aldwin.core.logging import setup_logging
from aldwin.core.settings import load_config
from aldwin.core.workspace import DEFAULT_WORKSPACE_DIRNAME
from aldwin.host import (
    AldwinHost,
    AskFn,
    InProcessTransport,
    RequestScope,
    SessionStore,
    format_args_preview,
)
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    ErrorEvent,
    Event,
    HelloRequest,
    HistoryRequest,
    SetModelRequest,
    TextEvent,
    TokenUsage,
    ToolCallEvent,
    ToolResultEvent,
    TurnRequest,
)


class GatedCommandTool(Tool):
    """Test tool that awaits `ask` before running."""

    name = "run_command"
    description = "Run a shell command after approval."
    parameters = {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "Shell command"}},
        "required": ["command"],
    }

    def __init__(self, ask: AskFn) -> None:
        super().__init__()
        self._ask = ask
        self.executed_commands: list[str] = []

    async def forward(self, *, command: str) -> str:  # type: ignore[override]
        approved = await self._ask("Run this command?", command)
        if not approved:
            return "user declined"
        self.executed_commands.append(command)
        return f"ran: {command}"


async def _collect_turn(client: AldwinClient, text: str) -> list[Event]:
    events: list[Event] = []
    async for ev in client.turn(text):
        events.append(ev)
    return events


async def test_request_scope_enforces_exactly_one_terminal_event() -> None:
    """`RequestScope` prevents double terminal events and handles crashes."""
    emitted: list[Event] = []

    async def record(event: Event) -> None:
        emitted.append(event)

    async with RequestScope("req_1", record) as scope:
        await scope.emit(TextEvent(re="req_1", delta="hi"))
        with pytest.raises(ValueError, match="must match"):
            await scope.emit(TextEvent(re="wrong_id", delta="no"))
        with pytest.raises(RuntimeError, match="Use `done\\(\\)`"):
            await scope.emit(DoneEvent(re="req_1", reason="ok"))
        await scope.done("ok")

        with pytest.raises(RuntimeError, match="already emitted"):
            await scope.done("ok")
        with pytest.raises(RuntimeError, match="already emitted"):
            await scope.error("internal", "boom")
        with pytest.raises(RuntimeError, match="after terminal event"):
            await scope.emit(TextEvent(re="req_1", delta="late"))

    assert len(emitted) == 2
    assert isinstance(emitted[-1], DoneEvent)

    emitted.clear()
    async with RequestScope("req_crash", record):
        raise ValueError("unexpected crash")

    assert len(emitted) == 1
    assert isinstance(emitted[0], ErrorEvent)
    assert emitted[0].code == "internal"
    assert emitted[0].re == "req_crash"

    with pytest.raises(RuntimeError, match="without emitting a terminal event"):
        async with RequestScope("req_forgot", record):
            pass


async def test_requests_before_hello_are_rejected_and_version_negotiated(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Pre-`hello` requests and version mismatches are rejected."""
    ensure_home(aldwin_home)
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    handle = transport.connect()

    try:
        for req in (
            TurnRequest(id="r1", text="hi"),
            HistoryRequest(id="r2"),
            SetModelRequest(id="r3", name="gpt-4o"),
        ):
            await handle.send(req)
            ev = await transport.next_event()
            assert isinstance(ev, ErrorEvent)
            assert ev.re == req.id
            assert ev.code == "protocol"

        await handle.send(HelloRequest(id="r_bad_ver", v_min=3, v_max=4))
        ver_ev = await transport.next_event()
        assert isinstance(ver_ev, ErrorEvent)
        assert ver_ev.re == "r_bad_ver"
        assert ver_ev.code == "version_mismatch"

        await handle.send(TurnRequest(id="r_still_locked", text="hi"))
        locked_ev = await transport.next_event()
        assert isinstance(locked_ev, ErrorEvent)
        assert locked_ev.code == "protocol"

        await handle.send(HelloRequest(id="r_ok_ver", v_min=1, v_max=2))
        ok_ev = await transport.next_event()
        assert isinstance(ok_ev, DoneEvent)
        assert ok_ev.re == "r_ok_ver"
        assert ok_ev.reason == "ok"
    finally:
        await host.stop_host()


async def test_plain_answer_streams_five_deltas_then_done_with_usage(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """5 streamed deltas arrive in order (< 50 ms) then `done(answered)`."""
    ensure_home(aldwin_home)
    fake_model.add_turns(
        FakeTurn.text(
            ["Hel", "lo", " ", "wor", "ld"],
            usage=Usage(input_tokens=12, output_tokens=5),
        )
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        started = time.monotonic()
        first_delta_ms: float | None = None
        events: list[Event] = []

        async for ev in client.turn("Say hello"):
            if first_delta_ms is None and isinstance(ev, TextEvent):
                first_delta_ms = (time.monotonic() - started) * 1000
            events.append(ev)

        assert first_delta_ms is not None and first_delta_ms < 50.0
        text_deltas = [ev.delta for ev in events if isinstance(ev, TextEvent)]
        assert text_deltas == ["Hel", "lo", " ", "wor", "ld"]
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"
        assert events[-1].usage == TokenUsage(input_tokens=12, output_tokens=5)
    finally:
        await client.close()
        await host.stop_host()


async def test_tool_call_and_result_mapping_with_120_char_truncation(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Tool events carry 120-char previews; full output saved."""
    ensure_home(aldwin_home)

    @tool
    def inspect_system(query: str) -> str:
        """Inspect system info.

        Args:
            query: What to inspect.
        """
        return ("A" * 200) + "\nsecond line with full details"

    long_arg = "x" * 200
    fake_model.add_turns(
        FakeTurn.tool_call("inspect_system", {"query": long_arg}, call_id="c_42"),
        FakeTurn.text(["All good."]),
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model, tools=[inspect_system]),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "check system")

        kinds = [ev.kind for ev in events]
        assert kinds == ["tool_call", "tool_result", "text", "done"]

        tc_ev = events[0]
        tr_ev = events[1]
        assert isinstance(tc_ev, ToolCallEvent)
        assert tc_ev.call_id == "c_42"
        assert tc_ev.name == "inspect_system"
        assert len(tc_ev.args_preview) <= 120
        assert "\n" not in tc_ev.args_preview

        assert isinstance(tr_ev, ToolResultEvent)
        assert tr_ev.call_id == "c_42"
        assert tr_ev.ok is True
        assert len(tr_ev.summary) <= 120
        assert "\n" not in tr_ev.summary
        assert tr_ev.duration_ms >= 0

        stored = SessionStore().load_latest()
        raw_mem = stored.memory.dump_json()
        assert "second line with full details" in raw_mem
    finally:
        await client.close()
        await host.stop_host()


async def test_error_classification_saves_session_and_hides_tracebacks(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Model and internal errors emit friendly ErrorEvents."""
    ensure_home(aldwin_home)
    log_path = setup_logging(aldwin_home)

    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp_401 = httpx.Response(401, request=req)
    resp_429 = httpx.Response(429, request=req)

    fake_model.add_turns(
        FakeTurn.error(
            openai.AuthenticationError(
                "invalid api key",
                response=cast(Any, resp_401),
                body=None,
            )
        ),
        FakeTurn.error(
            openai.RateLimitError(
                "rate limit",
                response=cast(Any, resp_429),
                body=None,
            )
        ),
        FakeTurn.error(openai.APIConnectionError(request=cast(Any, req))),
        FakeTurn.error(RuntimeError("secret_internal_details")),
    )

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        expected_codes = [
            ("auth_failed", "aldwin setup"),
            ("rate_limited", "rate limit"),
            ("model_unavailable", "connection"),
            ("internal", "aldwin.log"),
        ]

        for expected_code, expected_phrase in expected_codes:
            events = await _collect_turn(client, f"trigger {expected_code}")
            assert len(events) == 1
            err = events[0]
            assert isinstance(err, ErrorEvent)
            assert err.code == expected_code
            assert expected_phrase in err.message.lower()
            assert "Traceback" not in err.message
            assert "secret_internal_details" not in err.message

        log_text = log_path.read_text(encoding="utf-8")
        assert "secret_internal_details" in log_text
    finally:
        await client.close()
        await host.stop_host()


async def test_stop_mid_stream_saves_partial_answer_and_resumes_cleanly(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """`stop` mid-stream emits `done(stopped)` and saves partial text."""
    ensure_home(aldwin_home)
    hang_turn = FakeTurn.hang(prefix_chunks=["Part ", "one"])
    fake_model.add_turns(
        hang_turn,
        FakeTurn.text(["Continued cleanly."]),
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        collected: list[Event] = []

        async def consume() -> None:
            async for ev in client.turn("Tell a long story"):
                collected.append(ev)

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(hang_turn.hang_started.wait(), timeout=1.0)

        t0 = time.monotonic()
        stop_reply = await client.stop()
        await asyncio.wait_for(consumer, timeout=1.0)
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0
        assert isinstance(stop_reply, DoneEvent)
        assert stop_reply.reason == "ok"

        assert [e.delta for e in collected if isinstance(e, TextEvent)] == [
            "Part ",
            "one",
        ]
        assert isinstance(collected[-1], DoneEvent)
        assert collected[-1].reason == "stopped"

        reloaded = SessionStore().load_latest()
        assert reloaded.meta.interrupted is False
        assert isinstance(reloaded.memory.steps[-1], FinalStep)
        assert reloaded.memory.steps[-1].answer == "Part one"

        follow_up = await _collect_turn(client, "continue")
        assert isinstance(follow_up[-1], DoneEvent)
        assert follow_up[-1].reason == "answered"
        assert any(
            m.role == "assistant" and m.content == "Part one"
            for m in fake_model.received_messages
        )
    finally:
        await client.close()
        await host.stop_host()


async def test_approvals_yes_no_stop_and_unknown_approval_id(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Approval yes/no, stop during approval, unknown ID."""
    ensure_home(aldwin_home)
    cmd_tool_holder: list[GatedCommandTool] = []

    def build_agent(_model_name: str, ask: AskFn) -> Agent:
        t = GatedCommandTool(ask)
        cmd_tool_holder.append(t)
        return Agent(model=fake_model, tools=[t])

    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "df -h"}, call_id="c_yes"),
        FakeTurn.text(["41 GB free."]),
        FakeTurn.tool_call("run_command", {"command": "rm -rf /tmp/x"}, call_id="c_no"),
        FakeTurn.text(["Okay, I skipped that."]),
        FakeTurn.tool_call("run_command", {"command": "sleep 10"}, call_id="c_stop"),
        FakeTurn.text(["Resumed after stop."]),
    )

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=build_agent)
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        cmd_tool = cmd_tool_holder[0]

        # 1. Approve YES
        turn1_events: list[Event] = []
        async for ev in client.turn("check disk"):
            turn1_events.append(ev)
            if isinstance(ev, ApprovalEvent):
                res = await client.approve(ev.approval_id, "yes")
                assert isinstance(res, DoneEvent) and res.reason == "ok"

        assert cmd_tool.executed_commands == ["df -h"]
        assert isinstance(turn1_events[-1], DoneEvent)
        assert turn1_events[-1].reason == "answered"

        # 2. Approve NO
        turn2_events: list[Event] = []
        async for ev in client.turn("delete temp"):
            turn2_events.append(ev)
            if isinstance(ev, ApprovalEvent):
                res = await client.approve(ev.approval_id, "no")
                assert isinstance(res, DoneEvent) and res.reason == "ok"

        assert cmd_tool.executed_commands == ["df -h"]
        tool_results = [e for e in turn2_events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) == 1
        assert "user declined" in tool_results[0].summary

        # 3. Stop while waiting for approval
        turn3_events: list[Event] = []
        stale_approval_id = ""
        async for ev in client.turn("run sleep"):
            turn3_events.append(ev)
            if isinstance(ev, ApprovalEvent):
                stale_approval_id = ev.approval_id
                stop_res = await client.stop()
                assert isinstance(stop_res, DoneEvent) and stop_res.reason == "ok"

        assert cmd_tool.executed_commands == ["df -h"]
        assert isinstance(turn3_events[-1], DoneEvent)
        assert turn3_events[-1].reason == "stopped"

        # 4. Unknown / already-cancelled approval ID
        unknown_res = await client.approve(stale_approval_id, "yes")
        assert isinstance(unknown_res, ErrorEvent)
        assert unknown_res.code == "unknown_approval"

        # 5. Follow-up turn after mid-approval stop has valid messages
        turn4_events = await _collect_turn(client, "continue")
        assert isinstance(turn4_events[-1], DoneEvent)
        assert turn4_events[-1].reason == "answered"
        tool_result_ids = {
            m.tool_call_id for m in fake_model.received_messages if m.role == "tool"
        }
        for msg in fake_model.received_messages:
            for tc in msg.tool_calls:
                assert tc.id in tool_result_ids
    finally:
        await client.close()
        await host.stop_host()


async def test_second_turn_while_busy_returns_busy_error(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """A second `turn` while busy returns `error(busy)`; first turn finishes."""
    ensure_home(aldwin_home)
    hang_turn = FakeTurn.hang(prefix_chunks=["Working..."])
    fake_model.add_turns(hang_turn)

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        turn1_events: list[Event] = []

        async def run_first() -> None:
            async for ev in client.turn("first turn"):
                turn1_events.append(ev)

        t1 = asyncio.create_task(run_first())
        await asyncio.wait_for(hang_turn.hang_started.wait(), timeout=1.0)

        turn2_events = await _collect_turn(client, "second turn while busy")
        assert len(turn2_events) == 1
        assert isinstance(turn2_events[0], ErrorEvent)
        assert turn2_events[0].code == "busy"

        busy_model_res = await client.set_model("gpt-4.1")
        assert isinstance(busy_model_res, ErrorEvent)
        assert busy_model_res.code == "busy"

        hang_turn.hang_release.set()
        await asyncio.wait_for(t1, timeout=1.0)
        assert isinstance(turn1_events[-1], DoneEvent)
        assert turn1_events[-1].reason == "answered"
    finally:
        await client.close()
        await host.stop_host()


async def test_max_steps_maps_to_done_reason_max_steps(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Reaching `max_steps` emits `DoneEvent(reason='max_steps')`."""
    ensure_home(aldwin_home)

    @tool
    def ping(n: int) -> str:
        """Ping step `n`.

        Args:
            n: Step number.
        """
        return f"pong {n}"

    fake_model.add_turns(
        FakeTurn.tool_call("ping", {"n": 1}, call_id="c1"),
        FakeTurn.tool_call("ping", {"n": 2}, call_id="c2"),
        FakeTurn.text(["Forced final summary."]),
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(
            model=fake_model, tools=[ping], max_steps=2
        ),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "keep pinging")
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "max_steps"
        assert events[-1].usage is not None
    finally:
        await client.close()
        await host.stop_host()


async def test_set_model_switches_model_preserves_memory_and_rejects_unknown(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """`set_model` rebuilds agent, keeps memory, and rejects unknown models."""
    ensure_home(aldwin_home)
    built_models: list[str] = []

    def build_agent(model_name: str, _ask: AskFn) -> Agent:
        built_models.append(model_name)
        return Agent(model=fake_model)

    fake_model.add_turns(
        FakeTurn.text(["Hello Fola!"]),
        FakeTurn.text(["Still remember you on gpt-4.1!"]),
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=build_agent,
        available_models=["gpt-4o", "gpt-4.1"],
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        await _collect_turn(client, "My name is Fola")

        ok_res = await client.set_model("gpt-4.1")
        assert isinstance(ok_res, DoneEvent) and ok_res.reason == "ok"
        assert host.model == "gpt-4.1"
        assert load_config(aldwin_home).model == "gpt-4.1"
        assert built_models[-1] == "gpt-4.1"

        await _collect_turn(client, "Who am I?")
        assert any(
            m.role == "user" and m.content == "My name is Fola"
            for m in fake_model.received_messages
        )

        bad_res = await client.set_model("nonexistent-model")
        assert isinstance(bad_res, ErrorEvent)
        assert bad_res.code == "unknown_model"
        assert host.model == "gpt-4.1"
    finally:
        await client.close()
        await host.stop_host()


async def test_fifty_turn_conversation_one_terminal_event_each_and_fast_saves(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """50-turn run emits one terminal event per turn and saves in < 20 ms."""
    ensure_home(aldwin_home)

    @tool
    def quick_tool(step: int) -> str:
        """Run a quick tool step.

        Args:
            step: Turn index.
        """
        return f"step {step} ok"

    for i in range(50):
        if i % 5 == 0:
            fake_model.add_turns(
                FakeTurn.tool_call("quick_tool", {"step": i}, call_id=f"c_{i}"),
                FakeTurn.text([f"Answer {i}"]),
            )
        elif i == 25:
            fake_model.add_turns(FakeTurn.error(ModelError("transient 503")))
        else:
            fake_model.add_turns(FakeTurn.text([f"Reply {i}"]))

    save_durations_ms: list[float] = []
    base_store = SessionStore()
    orig_save = base_store.save

    def timed_save(*args: object, **kwargs: object) -> None:
        t0 = time.monotonic()
        orig_save(*args, **kwargs)  # type: ignore[arg-type]
        save_durations_ms.append((time.monotonic() - t0) * 1000)

    base_store.save = timed_save  # type: ignore[method-assign]

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        session_store=base_store,
        agent_builder=lambda _m, _ask: Agent(model=fake_model, tools=[quick_tool]),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        for i in range(50):
            events = await _collect_turn(client, f"Turn {i}")
            terminals = [ev for ev in events if isinstance(ev, (DoneEvent, ErrorEvent))]
            assert len(terminals) == 1
            assert events[-1] == terminals[0]

        sorted_saves = sorted(save_durations_ms)
        median_ms = sorted_saves[len(sorted_saves) // 2]
        assert median_ms < 20.0
    finally:
        await client.close()
        await host.stop_host()


async def test_client_connect_raises_protocol_error_on_version_mismatch(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """`client.connect` raises `ProtocolError` if handshake fails."""
    ensure_home(aldwin_home)
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        with pytest.raises(ProtocolError) as exc_info:
            await client.connect(v_min=3, v_max=4)
        assert exc_info.value.code == "version_mismatch"
    finally:
        await client.close()
        await host.stop_host()


async def test_turn_direct_final_answer_emits_text_with_zero_tools(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Single-step `final_answer` emits `TextEvent` and zero tool cards."""
    ensure_home(aldwin_home)
    fake_model.add_turns(
        FakeTurn.final_answer_tool("Direct reply via tool.", call_id="c_fa")
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "hi")
        kinds = [ev.kind for ev in events]
        assert kinds == ["text", "done"]
        assert isinstance(events[0], TextEvent)
        assert events[0].delta == "Direct reply via tool."
        assert isinstance(events[1], DoneEvent)
        assert events[1].reason == "answered"

        hist = await client.history()
        roles = [(item.role, item.text) for item in hist.messages]
        assert roles == [
            ("user", "hi"),
            ("assistant", "Direct reply via tool."),
        ]
    finally:
        await client.close()
        await host.stop_host()


async def test_turn_final_answer_with_streamed_prefix_does_not_duplicate(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """`final_answer` matching streamed text does not duplicate `TextEvent`."""
    ensure_home(aldwin_home)
    fake_model.add_turns(
        FakeTurn.final_answer_tool(
            "Same text",
            content="Same text",
            call_id="c_fa_dup",
        )
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "hi")
        texts = [ev.delta for ev in events if isinstance(ev, TextEvent)]
        assert texts == ["Same text"]
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"
    finally:
        await client.close()
        await host.stop_host()


async def test_turn_malformed_final_answer_error_recovers(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Malformed `final_answer({})` (`is_error=True`) continues to next step."""
    ensure_home(aldwin_home)
    fake_model.add_turns(
        FakeTurn.tool_call("final_answer", {}, call_id="c_bad_fa"),
        FakeTurn.text(["Recovered answer"]),
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "hi")
        assert not any(
            isinstance(ev, (ToolCallEvent, ToolResultEvent)) for ev in events
        )
        texts = [ev.delta for ev in events if isinstance(ev, TextEvent)]
        assert texts == ["Recovered answer"]
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"
    finally:
        await client.close()
        await host.stop_host()


def test_format_args_preview_unwraps_long_arguments_without_raw_json() -> None:
    """`format_args_preview` unwraps primary string fields before truncating."""
    long_cmd = "echo " + ("a" * 150)
    long_query = "python " + ("b" * 150)
    long_url = "https://example.com/" + ("c" * 150)

    cmd_preview = format_args_preview({"command": long_cmd})
    assert len(cmd_preview) <= 120
    assert cmd_preview.startswith("echo aaaa")
    assert cmd_preview.endswith("…")
    assert not cmd_preview.startswith("{")

    query_preview = format_args_preview({"query": long_query, "limit": 3})
    assert len(query_preview) <= 120
    assert query_preview.startswith('"python bbbb')
    assert query_preview.endswith("…")
    assert not query_preview.startswith("{")

    url_preview = format_args_preview({"url": long_url})
    assert len(url_preview) <= 120
    assert url_preview.startswith("https://example.com/cccc")
    assert url_preview.endswith("…")
    assert not url_preview.startswith("{")


async def test_approve_with_mismatched_digest_is_ignored_and_logged(
    aldwin_home: Path,
    tmp_path: Path,
    fake_model: FakeModel,
) -> None:
    """Right `approval_id` with wrong `digest` is ignored and logged."""
    ensure_home(aldwin_home)
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "echo hi"}, call_id="c_dig"),
        FakeTurn.text(["Done."]),
    )
    transport = InProcessTransport()
    host = AldwinHost(transport, home=aldwin_home, workspace=ws_dir, agent_builder=None)
    host._agent.model = fake_model
    host._agent_session = host._agent.start()
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events: list[Event] = []
        async for ev in client.turn("say hi"):
            events.append(ev)
            if isinstance(ev, ApprovalEvent):
                assert ev.digest.startswith("sha256:")
                # 1. Wrong digest -> ignored, logged as error, approval remains pending
                await client.approve(
                    ev.approval_id,
                    "yes",
                    digest="sha256:0000000000000000",
                )
                assert host._approvals.has_pending is True
                # 2. Matching digest -> resolves approval
                await client.approve(
                    ev.approval_id,
                    "yes",
                    digest=ev.digest,
                )

        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"
        log_text = (aldwin_home / "logs" / "approvals.jsonl").read_text(
            encoding="utf-8"
        )
        assert '"reason": "approval digest mismatch"' in log_text
    finally:
        await client.close()
        await host.stop_host()


async def test_three_denials_in_one_turn_triggers_circuit_breaker_and_resets(
    aldwin_home: Path,
    tmp_path: Path,
    fake_model: FakeModel,
) -> None:
    """Three denials in one turn stop the turn; count resets on next turn."""
    ensure_home(aldwin_home)
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "echo 1"}, call_id="c_d1"),
        FakeTurn.tool_call("run_command", {"command": "echo 2"}, call_id="c_d2"),
        FakeTurn.tool_call("run_command", {"command": "echo 3"}, call_id="c_d3"),
        FakeTurn.text(["Should not be reached in turn 1."]),
    )

    transport = InProcessTransport()
    host = AldwinHost(transport, home=aldwin_home, workspace=ws_dir)
    host._agent.model = fake_model
    host._agent_session = host._agent.start()
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        turn1_events: list[Event] = []
        async for ev in client.turn("run 3 commands"):
            turn1_events.append(ev)
            if isinstance(ev, ApprovalEvent):
                await client.approve(ev.approval_id, "no", digest=ev.digest)

        tool_results = [e for e in turn1_events if isinstance(e, ToolResultEvent)]
        assert len(tool_results) == 3
        assert tool_results[2].summary == "Stopped after 3 declined actions."
        assert isinstance(turn1_events[-1], DoneEvent)
        assert turn1_events[-1].reason == "stopped"

        # Turn 2: count resets to 0
        fake_model._turns.clear()
        fake_model.add_turns(
            FakeTurn.tool_call("run_command", {"command": "echo ok"}, call_id="c_ok"),
            FakeTurn.text(["Succeeded in turn 2."]),
        )
        turn2_events: list[Event] = []
        async for ev in client.turn("run 1 command"):
            turn2_events.append(ev)
            if isinstance(ev, ApprovalEvent):
                await client.approve(ev.approval_id, "yes", digest=ev.digest)

        assert host.policy_session.turn_denials == 0
        assert isinstance(turn2_events[-1], DoneEvent)
        assert turn2_events[-1].reason == "answered"
    finally:
        await client.close()
        await host.stop_host()


async def test_v1_door_diff_denied_ask_works_and_disconnect_denies(
    aldwin_home: Path,
    tmp_path: Path,
    fake_model: FakeModel,
) -> None:
    """v1 door denies Diff, allows Ask, and disconnect denies."""
    ensure_home(aldwin_home)
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()

    fake_model.add_turns(
        FakeTurn.tool_call(
            "set_persona",
            {"new_text": "Be concise."},
            call_id="c_v1_diff",
        ),
        FakeTurn.text(["Persona was not changed."]),
        FakeTurn.tool_call(
            "run_command", {"command": "echo v1_ok"}, call_id="c_v1_ask"
        ),
        FakeTurn.text(["Command ran."]),
        FakeTurn.tool_call(
            "run_command",
            {"command": "echo disconnect"},
            call_id="c_disc",
        ),
    )

    transport = InProcessTransport()
    host = AldwinHost(transport, home=aldwin_home, workspace=ws_dir)
    host._agent.model = fake_model
    host._agent_session = host._agent.start()
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        # Negotiate v1 (`hello(v_min=1, v_max=1)`)
        await client.connect(v_min=1, v_max=1)
        assert host.negotiated_version == 1

        # Diff-tier call on v1 door is denied with `source="no_approver"`
        ev_diff = await _collect_turn(client, "update persona")
        assert not any(isinstance(e, ApprovalEvent) for e in ev_diff)
        tr_diff = [e for e in ev_diff if isinstance(e, ToolResultEvent)]
        assert len(tr_diff) == 1
        assert tr_diff[0].ok is False
        assert "no one to approve" in tr_diff[0].summary or (
            "no one was available to approve" in tr_diff[0].summary
        )

        # Ask-tier call on v1 door works with plain y/n without digest
        ev_ask: list[Event] = []
        async for ev in client.turn("run echo"):
            ev_ask.append(ev)
            if isinstance(ev, ApprovalEvent):
                assert ev.v == 1
                assert ev.digest == ""
                await client.approve(ev.approval_id, "yes")
        tr_ask = [e for e in ev_ask if isinstance(e, ToolResultEvent)]
        assert len(tr_ask) == 1 and tr_ask[0].ok is True

        # Door disconnects while approval is pending -> `source="no_approver"`
        async for ev in client.turn("disconnect mid approval"):
            if isinstance(ev, ApprovalEvent):
                await client.close()
                break
        await asyncio.sleep(0.05)
        log_text = (aldwin_home / "logs" / "approvals.jsonl").read_text(
            encoding="utf-8"
        )
        assert '"source": "no_approver"' in log_text
    finally:
        await client.close()
        await host.stop_host()


async def test_batch_step_decline_second_edit_runs_no_tools_in_step(
    aldwin_home: Path,
    tmp_path: Path,
    fake_model: FakeModel,
) -> None:
    """Step calls `read_file` + 2 `edit_file`s; `no` on 2nd runs none."""
    from aldwin.core.policy import wrap_tools_with_policy
    from aldwin.core.tools import ToolOutput

    ensure_home(aldwin_home)
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    (ws_dir / "a.txt").write_text("a0", encoding="utf-8")
    (ws_dir / "b.txt").write_text("b0", encoding="utf-8")

    executed: list[str] = []

    class _ReadTool(Tool):
        name = "read_file"
        description = "Read file."
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

        async def forward(self, path: str) -> ToolOutput:  # type: ignore[override]
            executed.append(f"read:{path}")
            return ToolOutput("a0", summary=f"Read {path}", ok=True)

    class _EditTool(Tool):
        name = "edit_file"
        description = "Edit file."
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        }

        async def forward(self, path: str, content: str) -> ToolOutput:  # type: ignore[override]
            executed.append(f"edit:{path}:{content}")
            return ToolOutput("edited", summary=f"Edit {path}", ok=True)

    fake_model.add_turns(
        FakeTurn(
            tool_calls=[
                ("c_r1", "read_file", {"path": "a.txt"}),
                ("c_e1", "edit_file", {"path": "a.txt", "content": "a1"}),
                ("c_e2", "edit_file", {"path": "b.txt", "content": "b1"}),
            ]
        ),
        FakeTurn.text(["Skipped edits."]),
    )

    transport = InProcessTransport()
    host = AldwinHost(transport, home=aldwin_home, workspace=ws_dir)
    wrapped = wrap_tools_with_policy(
        [_ReadTool(), _EditTool()],
        session=host.policy_session,
        ask=host.ask_policy,
        approval_log=host._approval_log,
    )
    host._agent = Agent(model=fake_model, tools=wrapped)
    host._agent_session = host._agent.start()
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        approvals_seen = 0
        events: list[Event] = []
        async for ev in client.turn("read and edit"):
            events.append(ev)
            if isinstance(ev, ApprovalEvent):
                approvals_seen += 1
                ans: Literal["yes", "no"] = "yes" if approvals_seen == 1 else "no"
                await client.approve(ev.approval_id, ans, digest=ev.digest)

        assert approvals_seen == 2
        assert executed == []
        results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(results) == 3
        assert all(not r.ok for r in results)
    finally:
        await client.close()
        await host.stop_host()


def test_host_falls_back_to_aldwin_workspace_when_cwd_is_user_home(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Building the host from `~` uses the fallback root instead of raising."""
    monkeypatch.chdir(Path.home())

    host = AldwinHost(InProcessTransport())

    expected = (aldwin_home.parent / DEFAULT_WORKSPACE_DIRNAME).resolve()
    assert host.workspace_summary().root == expected


def test_host_accepts_the_root_bootstrap_chose_from_user_home(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root `bootstrap_host` picks from `~` must construct a host without error."""
    from aldwin.host.in_process import bootstrap_host

    monkeypatch.chdir(Path.home())
    boot = bootstrap_host(plain=True)

    host = AldwinHost(InProcessTransport(), home=boot.home, workspace=boot.workspace)

    assert host.workspace_summary().root == boot.workspace
