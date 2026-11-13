"""Host transport seam: a door can detach and reattach without stopping the host."""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

import pytest
from agentling import Agent
from fakes.host_transport import FakeHostTransport
from fakes.model import FakeModel, FakeTurn
from fakes.tools import GatedCommandTool

import aldwin.host.in_process as in_process_module
import aldwin.host.transport as transport_module
from aldwin.core.home import ensure_home
from aldwin.core.policy import ApprovalRequest
from aldwin.host import AldwinHost, AskFn, HostTransport, InProcessTransport
from aldwin.host.connectors import ConnectorEvent
from aldwin.protocol import (
    ApprovalEvent,
    ApproveRequest,
    DoneEvent,
    Event,
    HelloRequest,
    NoticeEvent,
    ToolResultEvent,
    TurnRequest,
)

HOST_TRANSPORT_MEMBERS = (
    "closed",
    "attached",
    "receive_request",
    "send_event",
    "set_new_conversation_handler",
    "close",
)


def _uses_host_transport(transport: HostTransport) -> HostTransport:
    """Type-checks that a transport satisfies the host Protocol."""
    return transport


async def _start_host(
    aldwin_home: Path, fake_model: FakeModel
) -> tuple[AldwinHost, FakeHostTransport, list[GatedCommandTool]]:
    ensure_home(aldwin_home)
    tools: list[GatedCommandTool] = []

    def build_agent(_model_name: str, ask: AskFn) -> Agent:
        tool = GatedCommandTool(ask)
        tools.append(tool)
        return Agent(model=fake_model, tools=[tool])

    transport = FakeHostTransport()
    host = AldwinHost(transport, agent_builder=build_agent)
    await host.start()
    return host, transport, tools


async def _hello(transport: FakeHostTransport, req_id: str) -> None:
    await transport.push(HelloRequest(id=req_id))
    event = await transport.next_event()
    assert isinstance(event, DoneEvent) and event.re == req_id


async def _until_approval(transport: FakeHostTransport) -> ApprovalEvent:
    while True:
        event = await transport.next_event()
        if isinstance(event, ApprovalEvent):
            return event


async def _until_done(transport: FakeHostTransport, req_id: str) -> list[Event]:
    events: list[Event] = []
    while True:
        event = await transport.next_event()
        events.append(event)
        if isinstance(event, DoneEvent) and event.re == req_id:
            return events


def test_host_transport_protocol_is_exported() -> None:
    """Exported, handler type shared, no web stack imports.

    The contract also says events sent while detached are dropped.
    """
    assert HostTransport is transport_module.HostTransport
    assert (
        transport_module.NewConversationHandler
        is in_process_module.NewConversationHandler
    )

    source = inspect.getsource(transport_module)
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert imported == {
        "__future__",
        "typing",
        "aldwin.host.in_process",
        "aldwin.protocol",
    }
    for web_stack in ("fastapi", "starlette", "uvicorn", "websockets"):
        assert web_stack not in imported

    send_doc = inspect.getdoc(transport_module.HostTransport.send_event) or ""
    assert "dropped, not queued" in send_doc


def test_in_process_transport_satisfies_host_transport_surface() -> None:
    """The CLI transport and the test double both fit the Protocol."""
    for transport in (InProcessTransport(), FakeHostTransport()):
        checked = _uses_host_transport(transport)
        for member in HOST_TRANSPORT_MEMBERS:
            assert hasattr(checked, member), member


async def test_fake_detach_keeps_transport_open_and_requests_waiting() -> None:
    """Only `close()` ends `receive_request`."""
    transport = FakeHostTransport()

    transport.detach()
    assert transport.attached is False
    assert transport.closed is False
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(transport.receive_request(), 0.2)

    transport.attach()
    assert transport.attached is True

    await transport.close()
    assert transport.attached is False
    assert await transport.receive_request() is None


async def test_mid_approval_detach_and_reattach_answers_the_pending_approval(
    aldwin_home: Path, fake_model: FakeModel
) -> None:
    """A dropped door can come back and approve."""
    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "df -h"}, call_id="c_1"),
        FakeTurn.text(["41 GB free."]),
    )
    host, transport, tools = await _start_host(aldwin_home, fake_model)
    try:
        await _hello(transport, "hello_1")
        await transport.push(TurnRequest(id="turn_1", text="check disk"))
        approval = await _until_approval(transport)

        transport.detach()
        await asyncio.sleep(0.2)
        serve_task = host._serve_task
        assert serve_task is not None and not serve_task.done()
        assert host._approvals.has_pending
        assert tools[0].executed_commands == []

        transport.attach()
        await _hello(transport, "hello_2")
        assert host._welcomed

        await transport.push(
            ApproveRequest(
                id="approve_1", approval_id=approval.approval_id, answer="yes"
            )
        )
        events = await _until_done(transport, "turn_1")

        assert tools[0].executed_commands == ["df -h"]
        assert not host._approvals.has_pending
        results = [e for e in events if isinstance(e, ToolResultEvent)]
        assert results and results[0].ok
        assert isinstance(events[-1], DoneEvent) and events[-1].reason == "answered"
    finally:
        await host.stop_host()


async def test_new_approval_while_detached_fails_closed_and_host_keeps_serving(
    aldwin_home: Path, fake_model: FakeModel
) -> None:
    """With no door attached, a new approval is denied, not left waiting."""
    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "rm -rf /tmp/x"}, call_id="c_2"),
        FakeTurn.text(["Skipped."]),
    )
    host, transport, tools = await _start_host(aldwin_home, fake_model)
    try:
        await _hello(transport, "hello_1")
        transport.detach()
        await transport.push(TurnRequest(id="turn_1", text="clean up"))

        for _ in range(100):
            if any(
                isinstance(e, DoneEvent) and e.re == "turn_1"
                for e in transport.dropped_events
            ):
                break
            await asyncio.sleep(0.02)

        assert tools[0].executed_commands == []
        assert not host._approvals.has_pending
        assert not any(isinstance(e, ApprovalEvent) for e in transport.dropped_events)
        declined = [
            e for e in transport.dropped_events if isinstance(e, ToolResultEvent)
        ]
        assert declined and "user declined" in declined[0].summary

        transport.attach()
        await _hello(transport, "hello_2")
    finally:
        await host.stop_host()


async def test_shutdown_after_detach_ends_the_serve_loop(
    aldwin_home: Path, fake_model: FakeModel
) -> None:
    """Closing the transport is still the one way to stop serving."""
    host, transport, _tools = await _start_host(aldwin_home, fake_model)
    await _hello(transport, "hello_1")
    transport.detach()

    await host.stop_host()

    assert transport.closed
    serve_task = host._serve_task
    assert serve_task is not None and serve_task.done()


async def test_approval_door_connected_needs_attached_and_welcomed(
    aldwin_home: Path, fake_model: FakeModel
) -> None:
    """The door counts as connected only when attached and welcomed."""
    host, transport, _tools = await _start_host(aldwin_home, fake_model)
    try:
        for attached in (True, False):
            for welcomed in (True, False):
                transport.attach() if attached else transport.detach()
                host._welcomed = welcomed
                assert host._approval_door_connected() is (attached and welcomed)

        doc = inspect.getdoc(AldwinHost._approval_door_connected) or ""
        assert "grace" in doc
    finally:
        await host.stop_host()


async def test_every_approval_path_asks_the_door_connected_helper(
    aldwin_home: Path, fake_model: FakeModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Approvals key off `attached`, never `closed`, on every path."""
    host, transport, _tools = await _start_host(aldwin_home, fake_model)
    try:
        await _hello(transport, "hello_1")
        monkeypatch.setattr(host, "_approval_door_connected", lambda: False)
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
        answer = await host.ask_policy(request)
        assert answer.source == "no_approver"
        assert await host.ask("Run?", "ls") is False

        runner_source = inspect.getsource(AldwinHost)
        assert "not self._transport.closed and self._welcomed" not in runner_source
        assert runner_source.count("self._approval_door_connected()") >= 4
    finally:
        await host.stop_host()


async def test_connector_events_only_go_to_an_attached_door(
    aldwin_home: Path, fake_model: FakeModel
) -> None:
    """Connector notices skip a detached door and reach an attached one."""
    host, transport, _tools = await _start_host(aldwin_home, fake_model)
    try:
        await _hello(transport, "hello_1")

        transport.detach()
        await host._emit_connector_event(ConnectorEvent(kind="notice", message="a"))
        assert transport.dropped_events == []

        transport.attach()
        await host._emit_connector_event(ConnectorEvent(kind="notice", message="b"))
        event = await transport.next_event()
        assert isinstance(event, NoticeEvent) and event.message == "b"
    finally:
        await host.stop_host()


def test_protocol_doc_states_the_host_transport_contract() -> None:
    """The docs say `None` means shutdown and a peer drop is `attached`."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "protocol.md").read_text(
        encoding="utf-8"
    )
    assert "HostTransport" in doc
    assert "never return `None` because a peer disconnected" in doc
    assert "`attached`" in doc
