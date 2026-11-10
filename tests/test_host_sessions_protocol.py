"""`sessions.list`, `sessions.open` and `sessions.new` against a real host."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from agentling import Agent
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient, ProtocolError
from aldwin.core.home import ensure_home
from aldwin.host import AldwinHost, InProcessTransport, SessionStore
from aldwin.protocol import (
    DoneEvent,
    Event,
    HistoryEvent,
    HistoryItem,
    Request,
    SessionsEvent,
    SessionsNewRequest,
    SessionsOpenRequest,
)

Rig = tuple[AldwinHost, AldwinClient]


@pytest.fixture
async def rig(aldwin_home: Path, fake_model: FakeModel) -> AsyncIterator[Rig]:
    ensure_home(aldwin_home)
    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=lambda _m, _ask: Agent(model=fake_model))
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        yield host, client
    finally:
        await client.close()
        await host.stop_host()


def _session_files(home: Path) -> list[str]:
    return sorted(p.stem for p in (home / "sessions").glob("*.json"))


async def _chat(client: AldwinClient, text: str) -> None:
    async for _event in client.turn(text):
        pass


async def _events(client: AldwinClient, request: Request) -> list[Event]:
    return [event async for event in client._stream(request)]


async def _start_hanging_turn(
    client: AldwinClient, fake_model: FakeModel
) -> asyncio.Task[None]:
    hang = FakeTurn.hang(prefix_chunks=["Working"])
    fake_model.add_turns(hang)
    task = asyncio.create_task(_chat(client, "long job"))
    await asyncio.wait_for(hang.hang_started.wait(), timeout=2.0)
    return task


async def test_sessions_list_emits_items_and_current(
    rig: Rig, fake_model: FakeModel
) -> None:
    """TC-17: `items` holds conversations with turns; `current` is the active one."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Hi there."]))
    await _chat(client, "Hello")
    first_id = host.stored_session.meta.id

    fresh = await client.sessions_new()
    listing = await client.sessions_list()

    assert [item.id for item in listing.items] == [first_id]
    assert listing.items[0].title == "Hello"
    assert listing.current is not None and fresh.current is not None
    assert listing.current.id == fresh.current.id != first_id
    assert listing.current.title == ""


async def test_sessions_open_idle_switches_active(
    rig: Rig, fake_model: FakeModel
) -> None:
    """TC-18: opening a saved conversation makes it active, memory and all."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Paris."]), FakeTurn.text(["Blue."]))
    await _chat(client, "Capital of France?")
    first_id = host.stored_session.meta.id
    await client.sessions_new()
    await _chat(client, "Colour of the sky?")

    opened = await client.sessions_open(first_id)

    assert opened.current is not None and opened.current.id == first_id
    assert opened.items == []
    assert host.stored_session.meta.id == first_id
    history = await client.history()
    assert history.messages == [
        HistoryItem(role="user", text="Capital of France?"),
        HistoryItem(role="assistant", text="Paris."),
    ]


async def test_sessions_open_labels_the_conversation_with_the_active_model(
    rig: Rig, aldwin_home: Path
) -> None:
    """An opened conversation shows the model that will answer it."""
    host, client = rig
    store = SessionStore(aldwin_home / "sessions")
    old = store.create_new(model="other/old-model")
    old.turns = [{"text": "Earlier", "skills": []}]
    store.save(old, old.memory, interrupted=False)
    assert host.model != "other/old-model"

    opened = await client.sessions_open(old.meta.id)
    listing = await client.sessions_list()

    assert opened.current is not None and opened.current.model == host.model
    assert listing.current is not None and listing.current.model == host.model


async def test_sessions_open_same_id_noop(
    rig: Rig, fake_model: FakeModel, aldwin_home: Path
) -> None:
    """TC-19: opening the active conversation succeeds and changes nothing."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Ok."]))
    await _chat(client, "Hi")
    before = host.stored_session
    raw_before = before.path.read_text("utf-8")

    opened = await client.sessions_open(before.meta.id)

    assert opened.current is not None and opened.current.id == before.meta.id
    assert host.stored_session is before
    assert before.path.read_text("utf-8") == raw_before
    assert len(_session_files(aldwin_home)) == 1


async def test_sessions_open_missing_returns_not_found(
    rig: Rig, aldwin_home: Path
) -> None:
    """TC-20: an unknown or corrupt id is `not_found`; the slot is unchanged."""
    host, client = rig
    active = host.stored_session.meta.id
    (aldwin_home / "sessions" / "2026-11-01_broken.json").write_text("{", "utf-8")

    for session_id in ("2026-11-01_nothere", "2026-11-01_broken", "../config"):
        with pytest.raises(ProtocolError) as exc:
            await client.sessions_open(session_id)
        assert exc.value.code == "not_found"
    assert host.stored_session.meta.id == active


async def test_sessions_open_mid_turn_busy(rig: Rig, fake_model: FakeModel) -> None:
    """TC-21: a switch while a turn runs is refused and the turn keeps going."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Done."]))
    await _chat(client, "First")
    first_id = host.stored_session.meta.id
    await client.sessions_new()
    active = host.stored_session.meta.id
    turn = await _start_hanging_turn(client, fake_model)

    with pytest.raises(ProtocolError) as exc:
        await client.sessions_open(first_id)

    assert exc.value.code == "busy"
    assert host.stored_session.meta.id == active
    assert not turn.done()
    await client.stop()
    await asyncio.wait_for(turn, timeout=2.0)


async def test_sessions_new_mid_turn_busy_no_new_file(
    rig: Rig, fake_model: FakeModel, aldwin_home: Path
) -> None:
    """TC-22: `sessions.new` mid-turn is refused and writes no file."""
    host, client = rig
    active = host.stored_session.meta.id
    turn = await _start_hanging_turn(client, fake_model)
    files_before = _session_files(aldwin_home)

    with pytest.raises(ProtocolError) as exc:
        await client.sessions_new()

    assert exc.value.code == "busy"
    assert _session_files(aldwin_home) == files_before
    assert host.stored_session.meta.id == active
    await client.stop()
    await asyncio.wait_for(turn, timeout=2.0)


async def test_sessions_new_reuses_empty_latest(rig: Rig, aldwin_home: Path) -> None:
    """TC-23: new chat on an empty conversation keeps that one."""
    host, client = rig
    landing = host.stored_session.meta.id

    first = await client.sessions_new()
    second = await client.sessions_new()

    assert first.current is not None and second.current is not None
    assert first.current.id == second.current.id == landing
    assert _session_files(aldwin_home) == [landing]


async def test_sessions_new_after_turns_creates_distinct_id(
    rig: Rig, fake_model: FakeModel, aldwin_home: Path
) -> None:
    """TC-24: once a conversation has turns, new chat starts an empty one."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Hello back."]))
    await _chat(client, "Hello")
    first_id = host.stored_session.meta.id

    fresh = await client.sessions_new()

    assert fresh.current is not None
    assert fresh.current.id != first_id
    assert fresh.current.title == ""
    assert _session_files(aldwin_home) == sorted([first_id, fresh.current.id])


async def test_sessions_new_switches_to_empty_latest_from_older_active(
    rig: Rig, fake_model: FakeModel, aldwin_home: Path
) -> None:
    """TC-25: from an older conversation, new chat goes to the empty newest one."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["One."]))
    await _chat(client, "Older chat")
    older = host.stored_session.meta.id
    empty = (await client.sessions_new()).current
    assert empty is not None
    await client.sessions_open(older)

    landed = await client.sessions_new()

    assert landed.current is not None and landed.current.id == empty.id
    assert host.stored_session.meta.id == empty.id
    assert _session_files(aldwin_home) == sorted([older, empty.id])


async def test_sessions_open_new_do_not_emit_history(
    rig: Rig, fake_model: FakeModel
) -> None:
    """TC-26: open and new reply with `sessions` only; `history` is a separate call."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Answer."]))
    await _chat(client, "Question")
    first_id = host.stored_session.meta.id

    new_events = await _events(client, SessionsNewRequest())
    open_events = await _events(client, SessionsOpenRequest(session_id=first_id))

    for events in (new_events, open_events):
        assert [type(e) for e in events] == [SessionsEvent, DoneEvent]
        assert not any(isinstance(e, HistoryEvent) for e in events)
    history = await client.history()
    assert [m.text for m in history.messages] == ["Question", "Answer."]


async def test_activate_persists_previous_before_switch(
    rig: Rig, fake_model: FakeModel
) -> None:
    """TC-27: leaving a conversation saves it first, without reordering history."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Noted."]))
    await _chat(client, "Remember this")
    leaving = host.stored_session
    saved_at = leaving.meta.updated_at
    host._todo = [{"text": "unsaved step", "status": "pending"}]

    await client.sessions_new()

    raw = json.loads(leaving.path.read_text("utf-8"))
    assert raw["todo"] == [{"text": "unsaved step", "status": "pending"}]
    assert raw["meta"]["updated_at"] == saved_at


async def test_client_sessions_helpers_happy_path(
    rig: Rig, fake_model: FakeModel
) -> None:
    """TC-28: the three client helpers round-trip through the host."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Hi."]))
    await _chat(client, "Hello")
    first_id = host.stored_session.meta.id

    listing = await client.sessions_list()
    fresh = await client.sessions_new()
    opened = await client.sessions_open(first_id)

    assert isinstance(listing, SessionsEvent)
    assert [i.id for i in listing.items] == [first_id]
    assert fresh.current is not None and fresh.current.id != first_id
    assert opened.current is not None and opened.current.id == first_id
    assert opened.current.model == host.stored_session.meta.model


async def test_idle_cli_new_conversation_uses_landing(
    rig: Rig, fake_model: FakeModel, aldwin_home: Path
) -> None:
    """TC-29: the terminal's `/new` follows the same no-litter rule when idle."""
    host, client = rig
    landing = host.stored_session.meta.id

    await client.new_conversation()
    await client.new_conversation()
    assert _session_files(aldwin_home) == [landing]

    fake_model.add_turns(FakeTurn.text(["Sure."]))
    await _chat(client, "Something")
    await client.new_conversation()
    assert host.stored_session.meta.id != landing
    assert len(_session_files(aldwin_home)) == 2


async def test_mid_turn_cli_new_cancels_protocol_new_busy(
    rig: Rig, fake_model: FakeModel
) -> None:
    """TC-30: mid-turn, `sessions.new` is refused but the terminal `/new` cancels."""
    host, client = rig
    turn = await _start_hanging_turn(client, fake_model)
    running = host.stored_session.meta.id

    with pytest.raises(ProtocolError) as exc:
        await client.sessions_new()
    assert exc.value.code == "busy"

    await client.new_conversation()

    await asyncio.wait_for(turn, timeout=2.0)
    assert host.stored_session.meta.id != running
    assert host.stored_session.meta.title == ""


def test_protocol_doc_lists_sessions_messages() -> None:
    """TC-31: the protocol doc names the sessions requests and their rules."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "protocol.md").read_text(
        encoding="utf-8"
    )
    for name in ("`sessions.list`", "`sessions.open`", "`sessions.new`"):
        assert name in doc
    assert "`history`" in doc and "busy" in doc
