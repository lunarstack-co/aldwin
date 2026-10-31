"""Tests for `aldwin.protocol` messages, unions, and wire rules."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aldwin.host import InProcessTransport
from aldwin.protocol import (
    PROTOCOL_VERSION,
    VERSION,
    ApprovalEvent,
    ApproveRequest,
    DoneEvent,
    DroppedSkill,
    ErrorEvent,
    Event,
    HelloRequest,
    HistoryEvent,
    HistoryItem,
    HistoryRequest,
    NoticeEvent,
    Request,
    SetModelRequest,
    SkillBodyEvent,
    SkillCatalogItem,
    SkillsEvent,
    SkillsListRequest,
    SkillsUsedEvent,
    StatusEvent,
    StopRequest,
    TextEvent,
    TodoGetRequest,
    TodoItem,
    TodoUpdatedEvent,
    TokenUsage,
    ToolCallEvent,
    ToolResultEvent,
    TurnRequest,
    parse_event,
    parse_message,
    parse_request,
)


@pytest.mark.parametrize(
    "request_msg",
    [
        HelloRequest(id="req_1", v_min=1, v_max=1),
        TurnRequest(id="req_2", text="How much disk space is free?"),
        StopRequest(id="req_3"),
        ApproveRequest(id="req_4", approval_id="ap_123", answer="yes"),
        HistoryRequest(id="req_5", limit=20),
        SetModelRequest(id="req_6", name="gpt-4o"),
    ],
)
def test_every_request_round_trips_json(request_msg: Request) -> None:
    """Every Request message round-trips through JSON."""
    raw = request_msg.model_dump_json()
    parsed = parse_request(raw)
    assert parsed == request_msg
    assert parsed.v == PROTOCOL_VERSION
    assert parsed.channel == "cli"
    assert parsed.sender == "cli"
    assert parsed.id.startswith("req_")
    assert parse_message(raw) == request_msg


@pytest.mark.parametrize(
    "event_msg",
    [
        TextEvent(re="req_1", delta="Hello "),
        ToolCallEvent(
            re="req_1",
            call_id="call_1",
            name="run_command",
            args_preview='{"command": "df -h"}',
        ),
        ToolResultEvent(
            re="req_1",
            call_id="call_1",
            ok=True,
            summary="41 GB free",
            duration_ms=120,
        ),
        ApprovalEvent(
            re="req_1",
            approval_id="ap_123",
            summary="Run this command?",
            detail="df -h",
        ),
        HistoryEvent(
            re="req_5",
            messages=[
                HistoryItem(role="user", text="hi", at="2026-10-17T10:00:00Z"),
                HistoryItem(role="assistant", text="hello", at="2026-10-17T10:00:01Z"),
            ],
            interrupted=True,
        ),
        StatusEvent(
            re="req_1",
            message="OpenAI is busy, retrying (2 of 3)…",
        ),
        DoneEvent(
            re="req_1",
            reason="answered",
            usage=TokenUsage(input_tokens=42, output_tokens=18),
        ),
        ErrorEvent(
            re="req_1",
            code="auth_failed",
            message="Your OpenAI key was rejected. Run `aldwin setup`.",
        ),
    ],
)
def test_every_event_round_trips_json(event_msg: Event) -> None:
    """Every Event message round-trips through JSON."""
    raw = event_msg.model_dump_json()
    parsed = parse_event(raw)
    assert parsed == event_msg
    assert parsed.v == PROTOCOL_VERSION
    assert parsed.type == "event"
    assert parsed.channel == "cli"
    assert parsed.sender == "host"
    assert parsed.re.startswith("req_")
    assert parse_message(raw) == event_msg


def test_unknown_fields_are_ignored_in_requests_and_events() -> None:
    """Unknown fields are ignored in both directions."""
    req_json = json.dumps(
        {
            "v": 1,
            "type": "turn",
            "id": "req_extra",
            "text": "hello",
            "future_flag": True,
            "extra_nested": {"a": 1},
        }
    )
    req = parse_request(req_json)
    assert isinstance(req, TurnRequest)
    assert req.text == "hello"
    assert "future_flag" not in req.model_dump()

    ev_json = json.dumps(
        {
            "v": 1,
            "type": "event",
            "re": "req_extra",
            "kind": "done",
            "reason": "answered",
            "usage": {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 3},
            "future_metric": 99,
        }
    )
    ev = parse_event(ev_json)
    assert isinstance(ev, DoneEvent)
    assert ev.usage == TokenUsage(input_tokens=10, output_tokens=5)
    assert "future_metric" not in ev.model_dump()


@pytest.mark.parametrize(
    "bad_request_payload",
    [
        {"v": 1, "type": "unknown_type", "id": "req_1"},
        {
            "v": 1,
            "type": "approve",
            "id": "req_1",
            "approval_id": "ap_1",
            "answer": "maybe",
        },
        {"v": 1, "type": "history", "id": "req_1", "limit": 0},
        {"v": 1, "type": "set_model", "id": "req_1", "name": ""},
    ],
)
def test_invalid_requests_are_rejected(bad_request_payload: dict[str, object]) -> None:
    """Invalid request discriminators and field bounds raise ValidationError."""
    with pytest.raises(ValidationError):
        parse_request(json.dumps(bad_request_payload))


@pytest.mark.parametrize(
    "bad_event_payload",
    [
        {"v": 1, "type": "event", "re": "req_1", "kind": "unknown_kind"},
        {"v": 1, "type": "turn", "re": "req_1", "kind": "text", "delta": "hi"},
        {"v": 1, "type": "event", "kind": "text", "delta": "missing re"},
        {"v": 1, "type": "event", "re": "", "kind": "text", "delta": "empty re"},
        {
            "v": 1,
            "type": "event",
            "re": "req_1",
            "kind": "error",
            "code": "bad",
            "message": "x",
        },
    ],
)
def test_invalid_events_are_rejected(bad_event_payload: dict[str, object]) -> None:
    """Invalid event discriminators, missing `re`, or wrong `type` fail."""
    with pytest.raises(ValidationError):
        parse_event(json.dumps(bad_event_payload))


def test_protocol_imports_only_stdlib_and_pydantic() -> None:
    """`src/aldwin/protocol` imports only stdlib and `pydantic`."""
    protocol_dir = (
        Path(__file__).resolve().parent.parent / "src" / "aldwin" / "protocol"
    )
    allowed_roots = {"__future__", "functools", "typing", "uuid", "pydantic", "aldwin"}

    for py_file in protocol_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root in allowed_roots, (
                        f"Forbidden import {alias.name} in {py_file}"
                    )
                    assert not alias.name.startswith("aldwin.core")
                    assert not alias.name.startswith("agentling")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root in allowed_roots, (
                    f"Forbidden import {node.module} in {py_file}"
                )
                assert not node.module.startswith("aldwin.core")
                assert not node.module.startswith("agentling")


async def test_in_process_transport_round_trips_and_closes_cleanly() -> None:
    """`InProcessTransport` round-trips requests/events and closes cleanly."""
    new_conv_called = False

    async def on_new() -> None:
        nonlocal new_conv_called
        new_conv_called = True

    transport = InProcessTransport(on_new_conversation=on_new)
    client_handle = transport.connect()

    req1 = HelloRequest(id="req_h")
    req2 = TurnRequest(id="req_t", text="hello")
    await client_handle.send(req1)
    await client_handle.send(req2)

    assert await transport.receive_request() == req1
    assert await transport.receive_request() == req2

    ev1 = TextEvent(re="req_t", delta="world")
    ev2 = DoneEvent(re="req_t", reason="answered")
    await transport.send_event(ev1)
    await transport.send_event(ev2)
    await client_handle.new_conversation()
    assert new_conv_called is True

    await client_handle.close()

    collected: list[Event] = []
    async for ev in client_handle.events():
        collected.append(ev)

    assert collected == [ev1, ev2]
    assert await transport.receive_request() is None


async def test_in_process_transport_rejects_send_after_close() -> None:
    """Sending a request after `close` raises RuntimeError."""
    transport = InProcessTransport()
    client_handle = transport.connect()
    await transport.close()
    await transport.close()

    with pytest.raises(RuntimeError, match="closed"):
        await client_handle.send(HelloRequest(id="req_1"))

    await transport.send_event(DoneEvent(re="req_1", reason="ok"))


def test_protocol_v2_messages_round_trip() -> None:
    """Protocol v2 messages and approval fields round-trip through JSON."""
    assert PROTOCOL_VERSION == 2
    assert VERSION == 2

    v2_requests: list[Request] = [
        HelloRequest(id="req_h2", v_min=1, v_max=2),
        TurnRequest(id="req_t2", text="Fix typo", skills=["files"]),
        ApproveRequest(
            id="req_a2",
            approval_id="ap_12",
            answer="yes_session",
            digest="sha256:9f2c",
            reason="ignored on yes_session",
        ),
        ApproveRequest(
            id="req_a3",
            approval_id="ap_13",
            answer="no",
            digest="sha256:9f2c",
            reason="x" * 350,
        ),
        SkillsListRequest(id="req_sl"),
        TodoGetRequest(id="req_tg"),
    ]
    for req in v2_requests:
        parsed_req = parse_request(req.model_dump_json())
        assert parsed_req == req

    # Reason is stripped on non-"no" and capped to 280 chars on "no"
    ap_yes_sess = v2_requests[2]
    assert isinstance(ap_yes_sess, ApproveRequest)
    assert ap_yes_sess.reason is None

    ap_no = v2_requests[3]
    assert isinstance(ap_no, ApproveRequest)
    assert ap_no.reason == "x" * 280

    v2_events: list[Event] = [
        ApprovalEvent(
            re="req_t2",
            approval_id="ap_12",
            tool="edit_file",
            tier="diff",
            summary="Edit notes.md",
            detail="--- a/notes.md\n+++ b/notes.md",
            grantable=True,
            grant_scope="file",
            digest="sha256:9f2c",
            risky=["recursive delete"],
        ),
        TodoUpdatedEvent(
            re="req_t2",
            items=[
                TodoItem(text="Read bills", status="done"),
                TodoItem(text="Write CSV", status="in_progress"),
            ],
            done=1,
            total=2,
        ),
        SkillsEvent(
            re="req_sl",
            items=[
                SkillCatalogItem(
                    name="files",
                    description="Work with files safely.",
                    source="default",
                    flags=[],
                    last_used=None,
                )
            ],
        ),
        SkillsUsedEvent(
            re="req_t2",
            names=["files"],
            dropped=[DroppedSkill(name="missing", reason="unknown")],
        ),
        SkillBodyEvent(re="req_sl", name="files", body="# Files skill"),
        NoticeEvent(re="req_t2", message="skills reloaded"),
    ]
    for ev in v2_events:
        parsed_ev = parse_event(ev.model_dump_json())
        assert parsed_ev == ev


def test_tc46_tool_event_preview_fields() -> None:
    """`summary` and `preview` are optional and stay on protocol v2."""
    call_json = json.dumps(
        {
            "v": 2,
            "type": "event",
            "re": "req_1",
            "kind": "tool_call",
            "call_id": "c1",
            "name": "read_file",
            "args_preview": "a.txt",
        }
    )
    call = parse_event(call_json)
    assert isinstance(call, ToolCallEvent)
    assert call.summary == ""
    assert call.v == 2
    call_with_summary = call.model_copy(update={"summary": "Read a.txt"})
    round_call = parse_event(call_with_summary.model_dump_json())
    assert isinstance(round_call, ToolCallEvent)
    assert round_call.summary == "Read a.txt"
    assert round_call.v == 2

    result_json = json.dumps(
        {
            "v": 2,
            "type": "event",
            "re": "req_1",
            "kind": "tool_result",
            "call_id": "c1",
            "ok": True,
            "summary": "Read a.txt · lines 1–1 of 1",
        }
    )
    result = parse_event(result_json)
    assert isinstance(result, ToolResultEvent)
    assert result.preview == ""
    assert result.v == 2
    result_with_preview = result.model_copy(update={"preview": "1\trow"})
    round_result = parse_event(result_with_preview.model_dump_json())
    assert isinstance(round_result, ToolResultEvent)
    assert round_result.preview == "1\trow"
    assert round_result.summary == "Read a.txt · lines 1–1 of 1"
    assert round_result.v == 2


# --- connectors (`FR-23`) ----------------------------------------------------


def _connector_messages() -> list[Any]:
    from aldwin.protocol import (
        ConnectorInfo,
        ConnectorProbeEvent,
        ConnectorsAddRequest,
        ConnectorsDisableRequest,
        ConnectorsEnableRequest,
        ConnectorsEvent,
        ConnectorsListRequest,
        ConnectorsReconnectRequest,
        ConnectorsRemoveRequest,
        ConnectorStatusEvent,
        ConnectorsToolPolicyRequest,
        ConnectorsTrustHintsRequest,
        ConnectorToolInfo,
    )

    tool = ConnectorToolInfo(
        name="read_thing",
        model_name="mcp__fixture__read_thing",
        read_only=True,
        effective="auto",
    )
    return [
        ConnectorsListRequest(),
        ConnectorsAddRequest(url="https://x.example/mcp", token="t", dry_run=True),
        ConnectorsRemoveRequest(name="x"),
        ConnectorsEnableRequest(name="x"),
        ConnectorsDisableRequest(name="x"),
        ConnectorsReconnectRequest(name="x"),
        ConnectorsToolPolicyRequest(name="x", tool="t", setting="default"),
        ConnectorsTrustHintsRequest(name="x", trust=False),
        ConnectorsEvent(
            re="r",
            items=[ConnectorInfo(name="x", title="X", url="https://x", tools=[tool])],
            error="connectors.yaml: connectors must be a mapping",
        ),
        ConnectorStatusEvent(re="host", name="x", status="failed", retry_in_s=30),
        ConnectorProbeEvent(re="r", name="x", title="X", tools=[tool], read=1),
    ]


@pytest.mark.parametrize(
    "message", _connector_messages(), ids=lambda m: type(m).__name__
)
def test_connector_messages_round_trip(message: Any) -> None:
    parsed = parse_message(message.model_dump_json())
    assert parsed == message


def test_old_tool_call_json_still_parses() -> None:
    raw = '{"v":2,"type":"event","re":"r","kind":"tool_call","call_id":"c","name":"x"}'
    event = parse_event(raw)
    assert isinstance(event, ToolCallEvent)
    assert (event.connector, event.reason) == (None, None)


def test_connector_token_is_hidden_from_repr() -> None:
    from aldwin.protocol import ConnectorsAddRequest

    req = ConnectorsAddRequest(url="https://x.example/mcp", token="sk-canary-123")
    assert "sk-canary-123" not in repr(req)
    assert "sk-canary-123" not in str(req)


def test_tool_grant_scope_and_new_error_codes() -> None:
    from aldwin.protocol import HOST_EVENT_RE

    approval = ApprovalEvent(re="r", approval_id="a", summary="s", grant_scope="tool")
    assert parse_event(approval.model_dump_json()) == approval
    for code in ("connector", "auth_required"):
        error = ErrorEvent(re="r", code=code, message="m")
        assert parse_event(error.model_dump_json()) == error
    assert HOST_EVENT_RE == "host"
