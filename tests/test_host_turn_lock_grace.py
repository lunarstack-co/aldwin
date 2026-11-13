"""Turn-scoped conversation lock, approval reconnect grace and status pull."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from agentling import Agent
from agentling.memory import FinalStep, TaskStep
from fakes.host_transport import FakeHostTransport
from fakes.model import FakeModel, FakeTurn
from fakes.tools import GatedCommandTool

import aldwin.host.runner as runner_module
from aldwin.core.home import ensure_home
from aldwin.core.policy import ApprovalRequest
from aldwin.host import AldwinHost, AskFn, SessionStore
from aldwin.host.conversation_lock import ConversationLock
from aldwin.protocol import (
    ApprovalEvent,
    ApproveRequest,
    DoneEvent,
    ErrorEvent,
    Event,
    HelloRequest,
    StatusEvent,
    StatusRequest,
    StopRequest,
    TurnRequest,
)

GRACE_S = 0.3


@dataclass
class Rig:
    host: AldwinHost
    transport: FakeHostTransport
    home: Path
    model: FakeModel
    tools: list[GatedCommandTool]

    def lock_is_free(self) -> bool:
        lock = ConversationLock(self.home, self.host.stored_session.meta.id)
        free = lock.try_acquire()
        lock.release()
        return free


async def _start(home: Path, model: FakeModel) -> Rig:
    tools: list[GatedCommandTool] = []

    def build_agent(_model_name: str, ask: AskFn) -> Agent:
        tool = GatedCommandTool(ask)
        tools.append(tool)
        return Agent(model=model, tools=[tool])

    transport = FakeHostTransport()
    host = AldwinHost(transport, agent_builder=build_agent, home=home)
    await host.start()
    rig = Rig(host, transport, home, model, tools)
    await _send(rig, HelloRequest(id="hello"))
    return rig


@pytest.fixture(autouse=True)
def short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_module, "APPROVAL_RECONNECT_GRACE_S", GRACE_S)
    monkeypatch.setattr(runner_module, "ATTACH_POLL_S", 0.02)


@pytest.fixture
async def rig(aldwin_home: Path, fake_model: FakeModel) -> AsyncIterator[Rig]:
    ensure_home(aldwin_home)
    started = await _start(aldwin_home, fake_model)
    try:
        yield started
    finally:
        await started.host.stop_host()


async def _send(rig: Rig, request: HelloRequest | StatusRequest) -> list[Event]:
    await rig.transport.push(request)
    return await _until_terminal(rig, request.id)


async def _until_terminal(rig: Rig, req_id: str) -> list[Event]:
    events: list[Event] = []
    while True:
        event = await rig.transport.next_event()
        events.append(event)
        if isinstance(event, DoneEvent | ErrorEvent) and event.re == req_id:
            return events


async def _until(rig: Rig, kind: type[Event]) -> Event:
    while True:
        event = await rig.transport.next_event()
        if isinstance(event, kind):
            return event


async def _turn(rig: Rig, req_id: str, text: str = "hi") -> list[Event]:
    await rig.transport.push(TurnRequest(id=req_id, text=text))
    return await _until_terminal(rig, req_id)


async def _status(rig: Rig, req_id: str) -> StatusEvent:
    events = await _send(rig, StatusRequest(id=req_id))
    assert isinstance(events[-1], DoneEvent)
    status = [e for e in events if isinstance(e, StatusEvent)]
    assert len(status) == 1
    return status[0]


async def _wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    async def poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def _start_approval_turn(rig: Rig, req_id: str = "turn") -> ApprovalEvent:
    rig.model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "df -h"}, call_id="c_1"),
        FakeTurn.text(["Done."]),
    )
    await rig.transport.push(TurnRequest(id=req_id, text="check disk"))
    approval = await _until(rig, ApprovalEvent)
    assert isinstance(approval, ApprovalEvent)
    return approval


async def test_idle_host_holds_no_conversation_lock(rig: Rig) -> None:
    """An open, idle door never blocks the other door."""
    assert rig.lock_is_free()
    rig.model.add_turns(FakeTurn.text(["Hello."]))
    await _turn(rig, "t1")
    assert rig.lock_is_free()


async def test_second_door_mid_turn_is_busy(
    rig: Rig, aldwin_home: Path, fake_model: FakeModel
) -> None:
    """The lock is held through a waiting approval."""
    approval = await _start_approval_turn(rig)
    assert not rig.lock_is_free()

    other = await _start(aldwin_home, fake_model)
    try:
        assert other.host.stored_session.meta.id == rig.host.stored_session.meta.id
        events = await _turn(other, "other_turn")
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].code == "busy"
        assert events[-1].message == runner_module.CONVERSATION_BUSY_MESSAGE
    finally:
        await other.host.stop_host()

    await rig.transport.push(
        ApproveRequest(id="ok", approval_id=approval.approval_id, answer="yes")
    )
    await _until_terminal(rig, "turn")
    assert rig.lock_is_free()


async def test_same_process_second_turn_is_still_busy(rig: Rig) -> None:
    """A second turn from the same door keeps the old busy reply."""
    hang = FakeTurn.hang(prefix_chunks=["Working"])
    rig.model.add_turns(hang)
    await rig.transport.push(TurnRequest(id="t1", text="long"))
    await asyncio.wait_for(hang.hang_started.wait(), 2.0)

    events = await _turn(rig, "t2")

    assert isinstance(events[-1], ErrorEvent) and events[-1].code == "busy"
    assert "Esc" in events[-1].message
    await rig.transport.push(StopRequest(id="stop"))
    await _until_terminal(rig, "t1")


async def test_stop_mid_approval_releases_the_lock(rig: Rig) -> None:
    """Stopping a turn that waits on an approval frees the conversation."""
    await _start_approval_turn(rig)
    assert not rig.lock_is_free()

    await rig.transport.push(StopRequest(id="stop"))
    events = await _until_terminal(rig, "turn")

    assert isinstance(events[-1], DoneEvent) and events[-1].reason == "stopped"
    assert rig.lock_is_free()


async def test_detach_mid_approval_and_reattach_in_grace_approves(rig: Rig) -> None:
    """A door that comes back within the grace answers the approval."""
    approval = await _start_approval_turn(rig)

    rig.transport.detach()
    await asyncio.sleep(GRACE_S / 3)
    assert rig.host._approvals.has_pending
    rig.transport.attach()
    await _send(rig, HelloRequest(id="hello_again"))
    await rig.transport.push(
        ApproveRequest(id="ok", approval_id=approval.approval_id, answer="yes")
    )
    events = await _until_terminal(rig, "turn")

    assert rig.tools[-1].executed_commands == ["df -h"]
    assert isinstance(events[-1], DoneEvent) and events[-1].reason == "answered"
    assert rig.lock_is_free()


async def test_grace_expiry_denies_and_releases_the_lock(rig: Rig) -> None:
    """Past the grace the approval is denied and stays denied."""
    approval = await _start_approval_turn(rig)

    rig.transport.detach()
    await _wait_for(lambda: not rig.host._approvals.has_pending)
    await _wait_for(
        lambda: rig.host._turn_task is not None and rig.host._turn_task.done()
    )

    assert rig.tools[-1].executed_commands == []
    assert rig.lock_is_free()
    assert any(isinstance(e, DoneEvent) for e in rig.transport.dropped_events)

    rig.transport.attach()
    await _send(rig, HelloRequest(id="hello_again"))
    events = await _send_approve(rig, approval.approval_id)
    assert isinstance(events[-1], ErrorEvent)
    assert events[-1].code == "unknown_approval"


async def _send_approve(rig: Rig, approval_id: str) -> list[Event]:
    await rig.transport.push(
        ApproveRequest(id="late", approval_id=approval_id, answer="yes")
    )
    return await _until_terminal(rig, "late")


async def test_grace_arms_on_detach_during_a_turn_and_clears_on_attach(
    rig: Rig,
) -> None:
    """Detaching mid-turn opens the grace; reattaching closes it."""
    hang = FakeTurn.hang(prefix_chunks=["Working"])
    rig.model.add_turns(hang)
    await rig.transport.push(TurnRequest(id="t1", text="long"))
    await asyncio.wait_for(hang.hang_started.wait(), 2.0)

    rig.transport.detach()
    assert rig.host._approval_door_connected()
    assert rig.host._grace_deadline is not None

    rig.transport.attach()
    assert rig.host._approval_door_connected()
    assert rig.host._grace_deadline is None

    await rig.transport.push(StopRequest(id="stop"))
    await _until_terminal(rig, "t1")


async def test_idle_detach_does_not_arm_grace(rig: Rig) -> None:
    """With no turn and nothing waiting, a dropped door is just gone."""
    rig.transport.detach()
    assert not rig.host._approval_door_connected()
    assert rig.host._grace_deadline is None


async def test_closed_transport_gets_no_grace(rig: Rig) -> None:
    """A transport closed for good fails approvals closed at once."""
    hang = FakeTurn.hang(prefix_chunks=["Working"])
    rig.model.add_turns(hang)
    await rig.transport.push(TurnRequest(id="t1", text="long"))
    await asyncio.wait_for(hang.hang_started.wait(), 2.0)

    await rig.transport.close()

    assert not rig.host._approval_door_connected()


async def test_session_written_by_another_door_is_reloaded(
    rig: Rig, aldwin_home: Path
) -> None:
    """A turn saved by the other door is in memory before the next turn."""
    rig.model.add_turns(FakeTurn.text(["First answer."]))
    await _turn(rig, "t1", "first")

    store = SessionStore(aldwin_home / "sessions")
    other = store.load_file(rig.host.stored_session.path)
    assert other is not None
    other.memory.add(TaskStep(task="from the other door"))
    other.memory.add(FinalStep(answer="Other answer."))
    assert other.turns is not None
    other.turns.append({"text": "from the other door", "skills": []})
    store.save(other, other.memory, interrupted=False)

    rig.model.add_turns(FakeTurn.text(["Second answer."]))
    await _turn(rig, "t2", "second")

    turns = rig.host.stored_session.turns or []
    assert [t["text"] for t in turns] == ["first", "from the other door", "second"]
    saved = json.loads(rig.host.stored_session.path.read_text("utf-8"))
    assert [t["text"] for t in saved["turns"]] == [t["text"] for t in turns]


async def test_unchanged_session_is_not_reloaded(rig: Rig) -> None:
    """When nothing else wrote the file, the live session is kept."""
    rig.model.add_turns(FakeTurn.text(["One."]), FakeTurn.text(["Two."]))
    await _turn(rig, "t1")
    session = rig.host._agent_session

    await _turn(rig, "t2")

    assert rig.host._agent_session is session


async def test_corrupt_session_on_reload_fails_the_turn(rig: Rig) -> None:
    """An unreadable conversation file fails the turn and frees the lock."""
    rig.model.add_turns(FakeTurn.text(["One."]))
    await _turn(rig, "t1")
    rig.host.stored_session.path.write_text("{ broken", encoding="utf-8")

    events = await _turn(rig, "t2")

    assert isinstance(events[-1], ErrorEvent)
    assert rig.host.stored_session.path.name in events[-1].message
    assert rig.lock_is_free()
    assert rig.host._active_turn_id is None


async def test_status_mid_turn_reports_progress_without_the_lock(rig: Rig) -> None:
    """Status answers mid-turn with counts and text so far."""
    hang = FakeTurn.hang(prefix_chunks=["Part ", "one"])
    rig.model.add_turns(hang)
    await rig.transport.push(TurnRequest(id="t1", text="long"))
    await asyncio.wait_for(hang.hang_started.wait(), 2.0)
    await asyncio.sleep(0.05)

    status = await _status(rig, "s1")

    assert status.turn_streamed_chunks == 2
    assert status.partial_text == "Part one"
    assert status.active_tool is None
    assert not rig.lock_is_free()
    await rig.transport.push(StopRequest(id="stop"))
    await _until_terminal(rig, "t1")


async def test_status_names_the_tool_waiting_on_approval(rig: Rig) -> None:
    """While a tool waits for approval, status names it."""
    approval = await _start_approval_turn(rig)

    status = await _status(rig, "s1")

    assert status.active_tool == "run_command"
    await rig.transport.push(
        ApproveRequest(id="ok", approval_id=approval.approval_id, answer="no")
    )
    await _until_terminal(rig, "turn")


async def test_idle_status_is_empty_and_turn_end_clears_it(rig: Rig) -> None:
    """No turn means zero counts, also right after a turn."""
    idle = await _status(rig, "s0")
    assert (idle.turn_streamed_chunks, idle.active_tool, idle.partial_text) == (
        0,
        None,
        None,
    )
    assert rig.lock_is_free()

    rig.model.add_turns(FakeTurn.text(["Some", " text."]))
    await _turn(rig, "t1")
    after = await _status(rig, "s1")

    assert (after.turn_streamed_chunks, after.partial_text) == (0, None)


async def test_pushed_retry_status_keeps_message_and_adds_progress(rig: Rig) -> None:
    """A retry notice mid-turn also carries the turn's progress."""
    hang = FakeTurn.hang(prefix_chunks=["Part"])
    rig.model.add_turns(hang)
    await rig.transport.push(TurnRequest(id="t1", text="long"))
    await asyncio.wait_for(hang.hang_started.wait(), 2.0)
    await asyncio.sleep(0.05)

    await rig.host.on_retry(1, 3, TimeoutError("slow"), 0.5)
    status = await _until(rig, StatusEvent)

    assert isinstance(status, StatusEvent)
    assert status.message
    assert status.turn_streamed_chunks == 1
    assert status.partial_text == "Part"
    await rig.transport.push(StopRequest(id="stop"))
    await _until_terminal(rig, "t1")


def test_docs_describe_status_and_turn_lock() -> None:
    """Protocol and privacy docs cover the status pull and locks."""
    root = Path(__file__).resolve().parent.parent / "docs"
    protocol = (root / "protocol.md").read_text(encoding="utf-8")
    privacy = (root / "privacy.md").read_text(encoding="utf-8")

    assert "`status`" in protocol
    assert "partial_text" in protocol
    assert "state/locks" in protocol
    assert "locks/" in privacy


async def test_approval_asked_outside_a_turn_still_expires(rig: Rig) -> None:
    """A connector flow's approval is denied after the grace, with no turn running."""
    request = ApprovalRequest(
        tool="run_command",
        tier="ask",
        summary="Run?",
        detail="ls",
        grantable=False,
        grant_key=None,
        risky=(),
        digest="d",
    )
    ask = asyncio.create_task(rig.host.ask_policy(request))
    await _until(rig, ApprovalEvent)

    rig.transport.detach()
    answer = await asyncio.wait_for(ask, GRACE_S * 4)

    assert answer.source == "no_approver"
    await _wait_for(
        lambda: rig.host._attach_watch is None or rig.host._attach_watch.done()
    )
    assert rig.host._grace_deadline is None


async def test_attach_watch_stops_when_nothing_is_running(rig: Rig) -> None:
    """The poll runs only while there is something to watch."""
    rig.model.add_turns(FakeTurn.text(["Hi."]))
    await _turn(rig, "t1")

    await _wait_for(
        lambda: rig.host._attach_watch is not None and rig.host._attach_watch.done()
    )
