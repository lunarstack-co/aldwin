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
    AppsUsedEvent,
    ConnectorInfo,
    ConnectorsAddRequest,
    DoneEvent,
    DroppedApp,
    DroppedSkill,
    ErrorEvent,
    Event,
    HelloRequest,
    HistoryApp,
    HistoryEvent,
    HistoryItem,
    HistoryRequest,
    ModelEntry,
    ModelsEvent,
    ModelsListRequest,
    NoticeEvent,
    ProviderKeyRequest,
    ProviderModels,
    ProviderServerRequest,
    Request,
    SessionMetaItem,
    SessionsEvent,
    SessionsListRequest,
    SessionsNewRequest,
    SessionsOpenRequest,
    SetModelRequest,
    SkillBodyEvent,
    SkillCatalogItem,
    SkillsEvent,
    SkillsListRequest,
    SkillsUsedEvent,
    StatusEvent,
    StatusRequest,
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
        ModelsListRequest(id="req_7", provider="openai", refresh=True),
        ProviderKeyRequest(id="req_8", provider="deepseek", key="sk-test-1234"),
        ProviderServerRequest(
            id="req_9",
            name="office-gpu",
            base_url="http://127.0.0.1:8000/v1",
            key="sk-srv-123",
            title="Office GPU",
            local=True,
        ),
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
                HistoryItem(
                    role="user",
                    text="hi",
                    at="2026-10-17T10:00:00Z",
                    apps=[HistoryApp(name="linear", title="Linear")],
                ),
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
        ModelsEvent(
            re="req_7",
            current="openai/gpt-4o",
            providers=[
                ProviderModels(
                    name="openai",
                    title="OpenAI",
                    local=False,
                    configured=True,
                    status="ok",
                    models=[
                        ModelEntry(
                            ref="openai/gpt-4o",
                            id="gpt-4o",
                            title="GPT-4o",
                            context_window=128000,
                            supports_tools=True,
                        )
                    ],
                )
            ],
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
    allowed_roots = {
        "__future__",
        "dataclasses",
        "functools",
        "re",
        "typing",
        "uuid",
        "pydantic",
        "aldwin",
    }

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


async def test_in_process_attached_true_while_open() -> None:
    """An open CLI transport is attached and not closed."""
    transport = InProcessTransport()
    assert transport.attached is True
    assert transport.closed is False


async def test_in_process_close_detaches_and_ends_requests() -> None:
    """Closing detaches for good and wakes `receive_request` with `None`."""
    transport = InProcessTransport()
    await transport.close()
    assert transport.attached is False
    assert transport.closed is True
    assert await transport.receive_request() is None


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
        TurnRequest(id="req_t3", text="File it", apps=["linear"]),
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


# --- connectors ---------------------------------------------------------


def _connector_messages() -> list[Any]:
    from aldwin.protocol import (
        ConnectorInfo,
        ConnectorProbeEvent,
        ConnectorsAddRequest,
        ConnectorsDisableRequest,
        ConnectorsEnableRequest,
        ConnectorsEvent,
        ConnectorsImportEvent,
        ConnectorsImportRequest,
        ConnectorsListRequest,
        ConnectorsReconnectRequest,
        ConnectorsRemoveRequest,
        ConnectorStatusEvent,
        ConnectorsToolPolicyRequest,
        ConnectorsTrustHintsRequest,
        ConnectorToolInfo,
        ImportRowInfo,
        ImportSourceInfo,
    )

    tool = ConnectorToolInfo(
        name="read_thing",
        model_name="mcp__fixture__read_thing",
        read_only=True,
        effective="auto",
    )
    return [
        ConnectorsListRequest(),
        ConnectorsImportRequest(paths=["/home/me/.cursor/mcp.json"], dry_run=True),
        ConnectorsImportEvent(
            re="r",
            found=[
                ImportSourceInfo(
                    app="Cursor",
                    scope="user",
                    path="/home/me/.cursor/mcp.json",
                    servers=2,
                )
            ],
            rows=[
                ImportRowInfo(
                    name="github",
                    kind="local",
                    status="imported",
                    detail="imported",
                    app="Cursor",
                )
            ],
            imported=1,
            values=1,
            warnings=["Zed: can't resolve {workspace}/.zed/settings.json"],
        ),
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


# --- connector sign-in ---------------------------------------------------------


def _sign_in_messages() -> list[Any]:
    from aldwin.protocol import (
        CatalogItem,
        ConnectorAuthEvent,
        ConnectorsAddRequest,
        ConnectorsAuthPasteRequest,
        ConnectorsAuthTokenRequest,
        ConnectorsEvent,
        ConnectorsLoginRequest,
        ConnectorsLogoutRequest,
        ConnectorStatusEvent,
    )

    return [
        ConnectorsLoginRequest(name="linear", no_browser=True),
        ConnectorsLogoutRequest(name="linear"),
        ConnectorsAuthPasteRequest(
            name="linear", redirect_url="http://127.0.0.1:47823/callback?code=c&state=s"
        ),
        ConnectorsAuthTokenRequest(name="github", token="t"),
        ConnectorsAddRequest(
            url="https://mcp.linear.app/mcp",
            auth="oauth",
            client_id="c",
            no_browser=True,
        ),
        ConnectorAuthEvent(
            re="r",
            name="linear",
            title="Linear",
            mode="browser",
            url="https://a/authorize?state=s",
            expires_in=300,
        ),
        ConnectorAuthEvent(
            re="r",
            name="github",
            title="GitHub",
            mode="token",
            token_help_url="https://github.com/settings/tokens",
            expires_in=300,
        ),
        ConnectorStatusEvent(re="host", name="linear", status="needs_auth"),
        ConnectorsEvent(
            re="r",
            catalog=[
                CatalogItem(
                    name="linear",
                    title="Linear",
                    purpose="p",
                    url="https://mcp.linear.app/mcp",
                    auth="oauth",
                    docs_url="https://d",
                    keywords=["linear"],
                    added=True,
                )
            ],
        ),
    ]


@pytest.mark.parametrize("message", _sign_in_messages(), ids=lambda m: type(m).__name__)
def test_sign_in_messages_round_trip(message: Any) -> None:
    assert parse_message(message.model_dump_json()) == message


def test_paste_and_token_are_hidden_from_repr() -> None:
    from aldwin.protocol import ConnectorsAuthPasteRequest, ConnectorsAuthTokenRequest

    paste = ConnectorsAuthPasteRequest(
        name="x", redirect_url="http://127.0.0.1/cb?code=SECRETCODE"
    )
    token = ConnectorsAuthTokenRequest(name="x", token="SECRETTOKEN")
    assert "SECRETCODE" not in repr(paste)
    assert "SECRETTOKEN" not in repr(token)


def test_connector_auth_needs_re() -> None:
    with pytest.raises(ValidationError):
        parse_event(
            '{"type":"event","kind":"connector.auth","name":"x","title":"X","mode":"token"}'
        )


def test_connectors_add_local_fields_compatible() -> None:
    old = parse_request(
        '{"v": 2, "type": "connectors.add", "id": "r1", "url": "https://a.example/mcp",'
        ' "name": "a", "surprise": 1}'
    )
    assert isinstance(old, ConnectorsAddRequest)
    assert (old.url, old.command, old.env_allow, old.cwd) == (
        "https://a.example/mcp",
        None,
        [],
        None,
    )
    local = ConnectorsAddRequest(command="npx -y pkg", env_allow=["GITHUB_TOKEN"])
    again = parse_request(local.model_dump_json())
    assert isinstance(again, ConnectorsAddRequest)
    assert (again.command, again.env_allow, again.url) == (
        "npx -y pkg",
        ["GITHUB_TOKEN"],
        None,
    )
    info = ConnectorInfo(
        name="everything",
        title="Everything",
        transport="stdio",
        command="npx -y @modelcontextprotocol/server-everything",
        note="review and enable",
    )
    assert ConnectorInfo.model_validate_json(info.model_dump_json()) == info
    assert (
        ConnectorInfo(name="a", title="A", url="https://a.example/mcp").transport
        == "http"
    )


# --- provider protocol messages -----------------------------------------------


def test_provider_key_request_hides_key_from_repr() -> None:
    """`ProviderKeyRequest` hides the secret key from repr and str."""
    req = ProviderKeyRequest(provider="deepseek", key="sk-canary-secret-12345")
    assert "sk-canary-secret-12345" not in repr(req)
    assert "sk-canary-secret-12345" not in str(req)


def test_provider_server_request_hides_key_and_round_trips() -> None:
    """`ProviderServerRequest` hides the secret key and round-trips."""
    req = ProviderServerRequest(
        name="office-gpu",
        base_url="http://192.168.1.50:8000/v1",
        key="sk-canary-token",
        title="Office GPU",
        local=True,
    )
    assert "sk-canary-token" not in repr(req)
    assert "sk-canary-token" not in str(req)
    dumped = req.model_dump_json()
    parsed = parse_request(dumped)
    assert isinstance(parsed, ProviderServerRequest)
    assert parsed.name == "office-gpu"
    assert parsed.base_url == "http://192.168.1.50:8000/v1"
    assert parsed.key == "sk-canary-token"
    assert parsed.title == "Office GPU"
    assert parsed.local is True


def test_models_list_and_models_event_round_trip() -> None:
    """`ModelsListRequest` and `ModelsEvent` round-trip through JSON."""
    req = ModelsListRequest(provider="openai", refresh=True)
    raw_req = req.model_dump_json()
    parsed_req = parse_request(raw_req)
    assert parsed_req == req

    event = ModelsEvent(
        re="req_test",
        current="openai/gpt-4o",
        providers=[
            ProviderModels(
                name="openai",
                title="OpenAI",
                local=False,
                configured=True,
                status="ok",
                models=[
                    ModelEntry(
                        ref="openai/gpt-4o",
                        id="gpt-4o",
                        title="GPT-4o",
                        context_window=128000,
                        supports_tools=True,
                    ),
                    ModelEntry(
                        ref="openai/o3-mini",
                        id="o3-mini",
                        title="o3-mini",
                        context_window=200000,
                        supports_tools=False,
                    ),
                ],
            )
        ],
    )
    raw_event = event.model_dump_json()
    parsed_event = parse_event(raw_event)
    assert parsed_event == event
    assert isinstance(parsed_event, ModelsEvent)
    assert parsed_event.providers[0].models[0].supports_tools is True
    assert parsed_event.providers[0].models[1].supports_tools is False


def _session_item(session_id: str) -> SessionMetaItem:
    return SessionMetaItem(
        id=session_id,
        created_at="2026-11-10T09:00:00+00:00",
        updated_at="2026-11-10T09:05:00+00:00",
        model="openai/gpt-4o",
        title="Plan the trip",
    )


def test_sessions_protocol_models_round_trip() -> None:
    """`sessions.*` requests and the `sessions` event survive JSON."""
    for request in (
        SessionsListRequest(id="req_l"),
        SessionsOpenRequest(id="req_o", session_id="2026-11-10_abcd1234"),
        SessionsNewRequest(id="req_n"),
    ):
        assert parse_request(request.model_dump_json()) == request
        assert parse_message(request.model_dump_json()) == request

    event = SessionsEvent(
        re="req_l",
        items=[_session_item("2026-11-10_abcd1234")],
        current=_session_item("2026-11-10_abcd1234"),
    )
    assert parse_event(event.model_dump_json()) == event
    empty = SessionsEvent(re="req_n")
    parsed = parse_event(empty.model_dump_json())
    assert isinstance(parsed, SessionsEvent)
    assert parsed.items == [] and parsed.current is None


def test_sessions_models_ignore_unknown_fields() -> None:
    """Newer doors and hosts may add fields without breaking parsing."""
    request = parse_request(
        json.dumps(
            {"type": "sessions.open", "id": "r1", "session_id": "s1", "pin": True}
        )
    )
    assert isinstance(request, SessionsOpenRequest)
    assert request.session_id == "s1"

    item = _session_item("s1").model_dump()
    item["preview"] = "later field"
    event = parse_event(
        json.dumps(
            {
                "type": "event",
                "kind": "sessions",
                "re": "r1",
                "items": [item],
                "current": item,
                "total": 1,
            }
        )
    )
    assert isinstance(event, SessionsEvent)
    assert event.items == [_session_item("s1")]


def test_sessions_open_requires_non_empty_id() -> None:
    """Opening needs a session id."""
    with pytest.raises(ValidationError):
        SessionsOpenRequest(session_id="")
    with pytest.raises(ValidationError):
        parse_request(json.dumps({"type": "sessions.open", "id": "r1"}))


def test_status_request_and_progress_fields_round_trip() -> None:
    """The status pull and its progress fields survive JSON."""
    request = StatusRequest(id="req_s")
    assert parse_request(request.model_dump_json()) == request

    event = StatusEvent(
        re="req_s",
        turn_streamed_chunks=3,
        active_tool="run_command",
        partial_text="Hello there",
    )
    assert parse_event(event.model_dump_json()) == event


def test_status_event_old_shape_still_parses() -> None:
    """A status event with only a message gets empty progress fields."""
    parsed = parse_event(
        json.dumps({"type": "event", "kind": "status", "re": "r1", "message": "x"})
    )
    assert isinstance(parsed, StatusEvent)
    assert (parsed.message, parsed.turn_streamed_chunks) == ("x", 0)
    assert parsed.active_tool is None and parsed.partial_text is None


def test_apps_used_round_trips_and_old_turns_have_no_apps() -> None:
    """`apps.used` round-trips; a turn without `apps` gets an empty list."""
    event = AppsUsedEvent(
        re="req_1",
        names=["linear"],
        dropped=[DroppedApp(name="slack", reason="unknown")],
    )
    assert parse_event(event.model_dump_json()) == event
    old = parse_request('{"v": 2, "type": "turn", "id": "req_1", "text": "hi"}')
    assert isinstance(old, TurnRequest)
    assert old.apps == []


def _display_models() -> list[Any]:
    from aldwin.protocol import (
        DeniedDisplay,
        DiffDisplay,
        DiffLine,
        FileEntry,
        FilesDisplay,
        GrantDisplay,
        MemoryChange,
        MemoryDisplay,
        PageDisplay,
        SearchDisplay,
        SearchHit,
        TableColumn,
        TableDisplay,
        TerminalDisplay,
    )

    return [
        TerminalDisplay(
            command="ls -la",
            cwd="/ws",
            stdout="total 8\n",
            stderr="",
            exit_code=0,
            duration_ms=14,
            truncated=False,
        ),
        DiffDisplay(
            path="notes.md",
            additions=1,
            deletions=1,
            lines=[
                DiffLine(kind="context", text="title"),
                DiffLine(kind="removed", text="old"),
                DiffLine(kind="added", text="new"),
            ],
            truncated=False,
        ),
        FilesDisplay(
            root="/ws",
            entries=[
                FileEntry(path="src", kind="folder"),
                FileEntry(path="README.md", kind="file", additions=3, deletions=1),
            ],
            truncated=False,
        ),
        SearchDisplay(
            query="python asyncio",
            results=[
                SearchHit(
                    title="asyncio docs",
                    url="https://docs.python.org/3/library/asyncio.html",
                    snippet="Asynchronous I/O.",
                )
            ],
        ),
        PageDisplay(
            url="https://example.com/post",
            title="Example Post",
            site="example.com",
            description="Short summary.",
        ),
        TableDisplay(
            columns=[
                TableColumn(key="name", label="Name"),
                TableColumn(key="count", label="Count"),
            ],
            rows=[{"name": "alpha", "count": "3"}],
            truncated=False,
        ),
        MemoryDisplay(
            changes=[
                MemoryChange(text="Prefers dark mode", change="added"),
                MemoryChange(text="Lives in London", change="replaced"),
            ]
        ),
        DeniedDisplay(
            rule="blocked_command",
            message="This command is blocked by safety rules.",
        ),
        GrantDisplay(
            scope="folder",
            reach=["/Users/me/Documents/tax"],
        ),
    ]


@pytest.mark.parametrize("display", _display_models(), ids=lambda d: d.kind)
def test_every_display_kind_round_trips_json(display: Any) -> None:
    """Every branch of the `Display` union round-trips on its own and on events."""
    from pydantic import TypeAdapter

    from aldwin.protocol import Display, validate_display

    adapter: TypeAdapter[Any] = TypeAdapter(Display)
    reparsed = adapter.validate_json(display.model_dump_json())
    assert reparsed == display
    assert reparsed.kind == display.kind
    assert validate_display(display.model_dump()) == display

    res_ev = ToolResultEvent(re="req_1", call_id="c1", ok=True, display=display)
    assert parse_event(res_ev.model_dump_json()) == res_ev

    ap_ev = ApprovalEvent(
        re="req_1",
        approval_id="ap_1",
        call_id="c1",
        summary="Approve?",
        display=display,
    )
    assert parse_event(ap_ev.model_dump_json()) == ap_ev


def test_unknown_display_kind_is_rejected() -> None:
    """An unknown `Display.kind` fails discriminated-union validation."""
    from pydantic import TypeAdapter

    from aldwin.protocol import Display

    adapter: TypeAdapter[Any] = TypeAdapter(Display)
    with pytest.raises(ValidationError) as exc_info:
        adapter.validate_python({"kind": "chart"})
    assert "chart" in str(exc_info.value) or "kind" in str(exc_info.value)


def test_display_ignores_extra_fields() -> None:
    """Display models ignore unknown fields for forward compatibility."""
    from pydantic import TypeAdapter

    from aldwin.protocol import Display, TerminalDisplay

    adapter: TypeAdapter[Any] = TypeAdapter(Display)
    parsed = adapter.validate_python(
        {"kind": "terminal", "command": "pwd", "colour": "red"}
    )
    assert isinstance(parsed, TerminalDisplay)
    assert parsed.command == "pwd"
    assert not hasattr(parsed, "colour")
    assert "colour" not in parsed.model_dump()


def test_old_tool_result_approval_and_done_shapes_parse_without_new_fields() -> None:
    """Older v2 events without `display`, `call_id` or `context` still parse."""
    tr = parse_event(
        '{"v":2,"type":"event","re":"r1","kind":"tool_result","call_id":"c1","ok":true}'
    )
    assert isinstance(tr, ToolResultEvent)
    assert tr.display is None

    ap = parse_event(
        '{"v":2,"type":"event","re":"r1","kind":"approval","approval_id":"a1","summary":"Run?"}'
    )
    assert isinstance(ap, ApprovalEvent)
    assert ap.call_id is None
    assert ap.display is None

    dn = parse_event(
        '{"v":2,"type":"event","re":"r1","kind":"done","reason":"answered"}'
    )
    assert isinstance(dn, DoneEvent)
    assert dn.context is None


def test_done_event_context_usage_round_trips() -> None:
    """`DoneEvent.context` round-trips with all 7 prompt breakdown segments."""
    from aldwin.protocol import ContextSegment, ContextUsage

    usage = ContextUsage(
        limit=128_000,
        used=5_000,
        segments=[
            ContextSegment(key="instructions", label="Instructions", tokens=1_000),
            ContextSegment(key="skills", label="Skills", tokens=200),
            ContextSegment(key="persona", label="Persona", tokens=100),
            ContextSegment(key="memory", label="Memory", tokens=0),
            ContextSegment(key="environment", label="Environment", tokens=50),
            ContextSegment(key="connectors", label="Apps", tokens=0),
            ContextSegment(key="conversation", label="Conversation", tokens=2_000),
            ContextSegment(key="tools", label="Tool definitions", tokens=1_650),
        ],
    )
    ev = DoneEvent(re="req_1", reason="answered", context=usage)
    parsed = parse_event(ev.model_dump_json())
    assert parsed == ev
    assert isinstance(parsed, DoneEvent)
    assert parsed.context == usage


def test_a_history_row_without_apps_parses_with_an_empty_list() -> None:
    item = HistoryItem.model_validate({"role": "user", "text": "hi"})
    assert item.apps == []
