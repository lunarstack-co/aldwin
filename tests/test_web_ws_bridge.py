"""The ``/v1/ws`` WebSocket: transport rules, upgrade checks and a real host."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from agentling import Agent
from anyio.from_thread import BlockingPortal
from fakes.model import FakeModel, FakeTurn
from fakes.tools import GatedCommandTool
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from aldwin.host import AldwinHost, AskFn
from aldwin.protocol import (
    ApprovalEvent,
    ApproveRequest,
    DoneEvent,
    ErrorEvent,
    Event,
    HelloRequest,
    HistoryRequest,
    ModelsListRequest,
    Request,
    SessionsListRequest,
    SkillsListRequest,
    StopRequest,
    TextEvent,
    TurnRequest,
    parse_event,
)
from aldwin.web.app import create_app
from aldwin.web.auth import Authenticator
from aldwin.web.runtime import WebHostRuntime
from aldwin.web.ws_transport import (
    CLOSE_ALREADY_ATTACHED,
    CLOSE_GOING_AWAY,
    CLOSE_REPLACED,
    CLOSE_UNSUPPORTED_DATA,
    REASON_ALREADY_ATTACHED,
    REASON_REPLACED,
    WebSocketTransport,
)

PORT = 7841
BASE = f"http://127.0.0.1:{PORT}"
# Relative URLs would resolve against ws://testserver and fail the Host check.
WS_URL = f"ws://127.0.0.1:{PORT}/v1/ws"
WEB_DIR = Path(__file__).resolve().parent.parent / "src" / "aldwin" / "web"


class FakeSocket:
    """Records what the transport sends and how it closes a tab."""

    def __init__(self, *, broken: bool = False) -> None:
        self.sent: list[str] = []
        self.closed_with: tuple[int, str] | None = None
        self.broken = broken

    async def send_text(self, text: str) -> None:
        if self.broken:
            raise RuntimeError("socket gone")
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


def _sock(**kwargs: Any) -> Any:
    return cast(Any, FakeSocket(**kwargs))


# Transport rules, without a server


async def test_detach_keeps_the_transport_open() -> None:
    """TC-14: a tab closing does not end the host's request loop."""
    transport = WebSocketTransport()
    tab = _sock()
    assert await transport.attach(tab)
    assert transport.attached

    transport.detach(tab)

    assert not transport.attached and not transport.closed
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(transport.receive_request(), 0.2)


async def test_close_wakes_the_host_with_none_and_closes_the_tab() -> None:
    """TC-15, TC-36: shutdown is the only way ``receive_request`` returns None."""
    transport = WebSocketTransport()
    tab = _sock()
    await transport.attach(tab)

    await transport.close()
    await transport.close()

    assert transport.closed and not transport.attached
    assert await transport.receive_request() is None
    assert tab.closed_with == (CLOSE_GOING_AWAY, "")
    late = _sock()
    assert not await transport.attach(late)
    assert late.closed_with == (CLOSE_GOING_AWAY, "")


async def test_events_reach_the_tab_and_are_dropped_while_detached() -> None:
    """TC-16: events go to the live tab, tagged web, and are never queued."""
    transport = WebSocketTransport()
    tab = _sock()
    await transport.attach(tab)
    await transport.send_event(TextEvent(re="t1", delta="hi"))
    transport.detach(tab)
    await transport.send_event(TextEvent(re="t1", delta="lost"))
    await transport.attach(tab)

    assert len(tab.sent) == 1
    event = parse_event(tab.sent[0])
    assert isinstance(event, TextEvent) and event.delta == "hi"
    assert event.channel == "web"


async def test_requests_are_tagged_as_coming_from_the_web() -> None:
    """TC-17: the page cannot claim to be the terminal."""
    transport = WebSocketTransport()
    tab = _sock()
    await transport.attach(tab)
    raw = HelloRequest(id="h1", channel="cli", sender="cli").model_dump_json()

    await transport.handle_text(tab, raw)

    request = await transport.receive_request()
    assert isinstance(request, HelloRequest)
    assert (request.id, request.channel, request.sender) == ("h1", "web", "web")


async def test_second_tab_is_refused_and_the_first_stays() -> None:
    """TC-18: without take-over, the new tab gets 4409 already_attached."""
    transport = WebSocketTransport()
    first, second = _sock(), _sock()
    await transport.attach(first)

    assert not await transport.attach(second)

    assert second.closed_with == (CLOSE_ALREADY_ATTACHED, REASON_ALREADY_ATTACHED)
    assert first.closed_with is None
    await transport.send_event(TextEvent(re="t1", delta="hi"))
    assert len(first.sent) == 1 and second.sent == []


async def test_take_over_replaces_the_old_tab() -> None:
    """TC-19: take-over closes the old tab with 4000 replaced."""
    transport = WebSocketTransport()
    old, new = _sock(), _sock()
    await transport.attach(old)

    assert await transport.attach(new, take_over=True)
    transport.detach(old)

    assert old.closed_with == (CLOSE_REPLACED, REASON_REPLACED)
    assert transport.attached
    await transport.send_event(TextEvent(re="t1", delta="hi"))
    assert len(new.sent) == 1 and old.sent == []
    old.closed_with = None
    await transport.handle_text(old, HelloRequest(id="stale").model_dump_json())
    assert old.closed_with == (CLOSE_REPLACED, REASON_REPLACED)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(transport.receive_request(), 0.1)


async def test_bad_json_gets_a_protocol_error_and_the_transport_lives() -> None:
    """TC-25: an unreadable message is answered, not fatal."""
    transport = WebSocketTransport()
    tab = _sock()
    await transport.attach(tab)

    await transport.handle_text(tab, "not json")
    await transport.handle_text(tab, json.dumps({"id": "x9", "type": "nope"}))
    await transport.handle_text(tab, HelloRequest(id="h1").model_dump_json())

    errors = [parse_event(text) for text in tab.sent]
    assert [(e.kind, e.re) for e in errors] == [("error", "host"), ("error", "x9")]
    assert all(isinstance(e, ErrorEvent) and e.code == "protocol" for e in errors)
    request = await transport.receive_request()
    assert isinstance(request, HelloRequest)


async def test_a_failed_send_detaches_the_tab() -> None:
    transport = WebSocketTransport()
    tab = _sock(broken=True)
    await transport.attach(tab)
    await transport.send_event(TextEvent(re="t1", delta="hi"))
    assert not transport.attached and not transport.closed


# Through the server, with a real host


@dataclass
class Rig:
    client: TestClient
    runtime: WebHostRuntime
    host: AldwinHost
    tools: list[GatedCommandTool]

    def ticket(self) -> str:
        response = self.client.post("/v1/ws-ticket", headers={"Origin": BASE})
        assert response.status_code == 200, response.text
        ticket: str = response.json()["ticket"]
        return ticket

    def connect(
        self,
        ticket: str | None = None,
        *,
        take_over: bool = False,
        origin: str | None = BASE,
    ) -> Any:
        query = f"ticket={ticket or self.ticket()}"
        if take_over:
            query += "&take_over=1"
        headers = {"Origin": origin} if origin is not None else {}
        return self.client.websocket_connect(f"{WS_URL}?{query}", headers=headers)


async def _start_host(
    runtime: WebHostRuntime, model: FakeModel
) -> tuple[AldwinHost, list[GatedCommandTool]]:
    tools: list[GatedCommandTool] = []

    def build_agent(_model_name: str, ask: AskFn) -> Agent:
        tool = GatedCommandTool(ask)
        tools.append(tool)
        return Agent(model=model, tools=[tool])

    host = AldwinHost(runtime.transport, agent_builder=build_agent)
    await host.start()
    return host, tools


def _portal(client: TestClient) -> BlockingPortal:
    """The event loop the app runs on; the host must start on the same one."""
    assert client.portal is not None
    return client.portal


def _app(runtime: WebHostRuntime, aldwin_home: Path, tmp_path: Path) -> TestClient:
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<main>Aldwin</main>", encoding="utf-8")
    auth = Authenticator.mint("127.0.0.1", PORT)
    app = create_app(
        authenticator=auth,
        home=aldwin_home,
        home_display="~",
        version="1",
        static_dir=static,
        runtime=runtime,
    )
    client = TestClient(app, base_url=BASE)
    client.__enter__()
    assert client.get(f"/?t={auth.token}").status_code == 200
    return client


@pytest.fixture
def rig(aldwin_home: Path, tmp_path: Path, fake_model: FakeModel) -> Iterator[Rig]:
    runtime = WebHostRuntime()
    client = _app(runtime, aldwin_home, tmp_path)
    try:
        host, tools = _portal(client).call(_start_host, runtime, fake_model)
        runtime.mark_ready()
        yield Rig(client, runtime, host, tools)
        _portal(client).call(host.stop_host)
    finally:
        client.__exit__(None, None, None)


def _send(ws: WebSocketTestSession, request: Request) -> None:
    ws.send_text(request.model_dump_json())


def _until(ws: WebSocketTestSession, re: str) -> list[Event]:
    """Collect events until the terminal event for request ``re``."""
    events: list[Event] = []
    while True:
        event = parse_event(ws.receive_text())
        events.append(event)
        if isinstance(event, DoneEvent | ErrorEvent) and event.re == re:
            return events


def _hello(ws: WebSocketTestSession, req_id: str = "hello_1") -> None:
    _send(ws, HelloRequest(id=req_id))
    events = _until(ws, req_id)
    assert isinstance(events[-1], DoneEvent)


def _expect_closed(ws: WebSocketTestSession) -> WebSocketDisconnect:
    """Return the close of a socket the server should have closed.

    A hello goes first, so a socket left open answers it and the test fails
    instead of waiting forever for a close that never comes.
    """
    _send(ws, HelloRequest(id="probe"))
    with pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    return closed.value


def _wait_for(check: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "condition never became true"
        time.sleep(0.02)


def test_turn_streams_over_the_socket(rig: Rig, fake_model: FakeModel) -> None:
    """TC-01: hello, then a turn whose text arrives as the host sends it."""
    fake_model.add_turns(FakeTurn.text(["Hello", " there"]))
    with rig.connect() as ws:
        _hello(ws)
        _send(ws, TurnRequest(id="turn_1", text="hi"))
        events = _until(ws, "turn_1")

    text = "".join(e.delta for e in events if isinstance(e, TextEvent))
    assert text == "Hello there"
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.reason == "answered"
    assert all(e.channel == "web" for e in events)


def test_stop_ends_a_running_turn(rig: Rig, fake_model: FakeModel) -> None:
    """TC-01: stop over the socket ends the turn with reason stopped."""
    fake_model.add_turns(FakeTurn.hang(prefix_chunks=["Thinking"]))
    with rig.connect() as ws:
        _hello(ws)
        _send(ws, TurnRequest(id="turn_1", text="think"))
        first = parse_event(ws.receive_text())
        assert isinstance(first, TextEvent)
        _send(ws, StopRequest(id="stop_1"))
        events = _until(ws, "turn_1")

    done = events[-1]
    assert isinstance(done, DoneEvent) and done.reason == "stopped"


def test_approval_is_answered_over_the_socket(rig: Rig, fake_model: FakeModel) -> None:
    """TC-01: an approval goes to the tab and the tab's yes runs the tool."""
    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "df -h"}, call_id="c_1"),
        FakeTurn.text(["41 GB free."]),
    )
    with rig.connect() as ws:
        _hello(ws)
        _send(ws, TurnRequest(id="turn_1", text="check disk"))
        approval = next(
            e
            for e in iter(lambda: parse_event(ws.receive_text()), None)
            if isinstance(e, ApprovalEvent)
        )
        _send(
            ws,
            ApproveRequest(id="ok_1", approval_id=approval.approval_id, answer="yes"),
        )
        events = _until(ws, "turn_1")

    assert rig.tools[0].executed_commands == ["df -h"]
    assert isinstance(events[-1], DoneEvent) and events[-1].reason == "answered"


def test_closing_the_tab_mid_approval_keeps_the_host_waiting(
    rig: Rig, fake_model: FakeModel
) -> None:
    """TC-20, TC-21: a dropped tab detaches; a new tab can answer the approval."""
    fake_model.add_turns(
        FakeTurn.tool_call("run_command", {"command": "ls"}, call_id="c_1"),
        FakeTurn.text(["Done."]),
    )
    with rig.connect() as ws:
        _hello(ws)
        _send(ws, TurnRequest(id="turn_1", text="list"))
        approval = next(
            e
            for e in iter(lambda: parse_event(ws.receive_text()), None)
            if isinstance(e, ApprovalEvent)
        )

    _wait_for(lambda: not rig.runtime.transport.attached)
    assert not rig.runtime.transport.closed
    serve_task = rig.host._serve_task
    assert serve_task is not None and not serve_task.done()
    assert rig.tools[0].executed_commands == []

    with rig.connect() as ws:
        _hello(ws, "hello_2")
        _send(
            ws,
            ApproveRequest(id="ok_1", approval_id=approval.approval_id, answer="yes"),
        )
        events = _until(ws, "turn_1")

    assert rig.tools[0].executed_commands == ["ls"]
    assert isinstance(events[-1], DoneEvent)


def test_second_tab_gets_4409_and_the_first_keeps_working(rig: Rig) -> None:
    """TC-04: one live tab; the second is closed with already_attached."""
    with rig.connect() as first:
        _hello(first)
        with rig.connect() as second:
            closed = _expect_closed(second)
        assert closed.code == CLOSE_ALREADY_ATTACHED
        assert closed.reason == REASON_ALREADY_ATTACHED
        _hello(first, "hello_2")


def test_take_over_moves_the_conversation_to_the_new_tab(rig: Rig) -> None:
    """TC-05: the old tab is closed with 4000 replaced; the new one is served."""
    with rig.connect() as old:
        _hello(old)
        with rig.connect(take_over=True) as new:
            closed = _expect_closed(old)
            assert closed.code == CLOSE_REPLACED
            assert closed.reason == REASON_REPLACED
            _hello(new, "hello_2")
            assert rig.runtime.transport.attached


def test_other_requests_ride_the_same_socket(rig: Rig) -> None:
    """TC-27, TC-28: history, skills, models and sessions.list get answers."""
    with rig.connect() as ws:
        _hello(ws)
        for request in (
            HistoryRequest(id="r_history"),
            SkillsListRequest(id="r_skills"),
            ModelsListRequest(id="r_models"),
            SessionsListRequest(id="r_sessions"),
        ):
            _send(ws, request)
            events = _until(ws, request.id)
            assert events[-1].re == request.id


def test_unreadable_message_is_answered_and_the_socket_stays(rig: Rig) -> None:
    """TC-25: bad JSON gets a protocol error on the same socket."""
    with rig.connect() as ws:
        ws.send_text("{not json")
        error = parse_event(ws.receive_text())
        assert isinstance(error, ErrorEvent) and error.code == "protocol"
        _hello(ws)


def test_binary_frames_close_the_socket(rig: Rig) -> None:
    """TC-26: the protocol is text JSON only."""
    with rig.connect() as ws:
        ws.send_bytes(b"\x00\x01")
        closed = _expect_closed(ws)
    assert closed.code == CLOSE_UNSUPPORTED_DATA
    _wait_for(lambda: not rig.runtime.transport.attached)
    assert not rig.runtime.transport.closed


def test_shutdown_closes_the_tab_and_stops_the_host(rig: Rig) -> None:
    """TC-36: closing the runtime ends the host loop and the socket."""
    with rig.connect() as ws:
        _hello(ws)
        _portal(rig.client).call(rig.runtime.shutdown)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == CLOSE_GOING_AWAY
    _wait_for(lambda: rig.host._serve_task is not None and rig.host._serve_task.done())
    assert rig.client.post("/v1/ws-ticket", headers={"Origin": BASE}).status_code == 503


# Upgrade checks


def _denied(rig: Rig, **kwargs: Any) -> int:
    with pytest.raises(WebSocketDenialResponse) as denied:
        with rig.connect(**kwargs):
            pass
    status: int = denied.value.status_code
    return status


def test_upgrade_without_a_good_ticket_is_401(rig: Rig) -> None:
    """TC-02: no ticket, or one that was never minted, is refused before accept."""
    with pytest.raises(WebSocketDenialResponse) as denied:
        with rig.client.websocket_connect(WS_URL, headers={"Origin": BASE}):
            pass
    assert denied.value.status_code == 401
    assert _denied(rig, ticket="made-up") == 401


def test_ticket_reuse_is_401(rig: Rig) -> None:
    """TC-03: a ticket opens one socket only."""
    ticket = rig.ticket()
    with rig.connect(ticket) as ws:
        _hello(ws)
    assert _denied(rig, ticket=ticket) == 401


@pytest.mark.parametrize(
    "origin",
    [None, "http://evil.example", "http://127.0.0.1:3000", "http://localhost:7842"],
)
def test_upgrade_from_another_page_is_403_and_keeps_the_ticket(
    rig: Rig, origin: str | None
) -> None:
    """TC-02, TC-22, TC-37: Origin must be present and this server's own."""
    ticket = rig.ticket()
    assert _denied(rig, ticket=ticket, origin=origin) == 403
    assert rig.runtime.tickets.consume(ticket)


@pytest.mark.parametrize(
    "headers",
    [{"Host": "evil.example:7841"}, {"Sec-Fetch-Site": "cross-site"}],
)
def test_upgrade_with_a_foreign_host_or_site_is_403(
    rig: Rig, headers: dict[str, str]
) -> None:
    """TC-02: the Host and Sec-Fetch-Site checks apply to the upgrade too."""
    ticket = rig.ticket()
    with pytest.raises(WebSocketDenialResponse) as denied:
        with rig.client.websocket_connect(
            f"{WS_URL}?ticket={ticket}", headers={"Origin": BASE, **headers}
        ):
            pass
    assert denied.value.status_code == 403
    assert rig.runtime.tickets.consume(ticket)


def test_localhost_origin_on_the_bound_port_is_accepted(rig: Rig) -> None:
    """TC-37: localhost and 127.0.0.1 on the same port are the same server."""
    with rig.connect(origin=f"http://localhost:{PORT}") as ws:
        _hello(ws)


def test_expired_ticket_is_401(aldwin_home: Path, tmp_path: Path) -> None:
    """TC-13: a ticket used after its lifetime is refused."""
    runtime = WebHostRuntime(ticket_ttl=0.05)
    runtime.mark_ready()
    client = _app(runtime, aldwin_home, tmp_path)
    try:
        ticket = client.post("/v1/ws-ticket", headers={"Origin": BASE}).json()["ticket"]
        time.sleep(0.1)
        with pytest.raises(WebSocketDenialResponse) as denied:
            with client.websocket_connect(
                f"{WS_URL}?ticket={ticket}", headers={"Origin": BASE}
            ):
                pass
        assert denied.value.status_code == 401
    finally:
        client.__exit__(None, None, None)


def test_socket_waits_for_a_host(aldwin_home: Path, tmp_path: Path) -> None:
    """TC-24: before a host is serving, the upgrade is refused with 503."""
    runtime = WebHostRuntime()
    client = _app(runtime, aldwin_home, tmp_path)
    try:
        ticket = runtime.tickets.mint()
        with pytest.raises(WebSocketDenialResponse) as denied:
            with client.websocket_connect(
                f"{WS_URL}?ticket={ticket}", headers={"Origin": BASE}
            ):
                pass
        assert denied.value.status_code == 503
    finally:
        client.__exit__(None, None, None)


# Boundaries


def test_web_door_has_no_jwt_or_auth_js_path() -> None:
    """TC-30, TC-35: sessions are the cookie and one-time tickets only."""
    source = "".join(p.read_text("utf-8").lower() for p in WEB_DIR.rglob("*.py"))
    for marker in ("jwt", "jwe", "nextauth", "auth.js", "auth_secret"):
        assert marker not in source, marker


def test_docs_describe_the_socket_and_its_ticket() -> None:
    """TC-33, TC-34: protocol and privacy pages cover the WebSocket."""
    docs = Path(__file__).resolve().parent.parent / "docs"
    protocol = (docs / "protocol.md").read_text(encoding="utf-8")
    section = protocol.split("## The browser's WebSocket", 1)[1].split("\n## ", 1)[0]
    for needle in ("/v1/ws", "4409", "already_attached", "take_over=1", "replaced"):
        assert needle in section, needle
    privacy = (docs / "privacy.md").read_text(encoding="utf-8")
    assert "works once and only for 30 seconds" in privacy


async def test_refusal_falls_back_to_a_close_code() -> None:
    """A server without HTTP refusals gets 4401/4403 close codes instead."""
    from aldwin.web.routes.ws import _refuse
    from aldwin.web.ws_transport import CLOSE_FORBIDDEN, CLOSE_UNAUTHORIZED

    class NoExtensions(FakeSocket):
        scope: dict[str, Any] = {"extensions": {}}

    for status, code in ((401, CLOSE_UNAUTHORIZED), (403, CLOSE_FORBIDDEN)):
        socket = NoExtensions()
        await _refuse(cast(Any, socket), status, code, "no")
        assert socket.closed_with == (code, "no")
