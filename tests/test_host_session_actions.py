"""`sessions.rename`, `sessions.pin` and `sessions.delete` against a real host."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from agentling import Agent
from fakes.model import FakeModel, FakeTurn
from pydantic import ValidationError

from aldwin.client import AldwinClient, ProtocolError
from aldwin.core.home import ensure_home
from aldwin.host import AldwinHost, InProcessTransport, SessionStore
from aldwin.host.conversation_lock import ConversationLock
from aldwin.host.runner import DELETED_ELSEWHERE
from aldwin.protocol import MAX_SESSION_TITLE_CHARS, NoticeEvent, SessionsRenameRequest

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


async def _chat(client: AldwinClient, text: str) -> None:
    async for _event in client.turn(text):
        pass


def _meta(path: Path) -> dict[str, object]:
    meta = json.loads(path.read_text("utf-8"))["meta"]
    assert isinstance(meta, dict)
    return meta


async def _two_conversations(rig: Rig, fake_model: FakeModel) -> tuple[Path, Path]:
    """An older saved conversation and the active one, both with a turn."""
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Paris."]), FakeTurn.text(["Blue."]))
    await _chat(client, "Capital of France?")
    older = host.stored_session.path
    await client.sessions_new()
    await _chat(client, "Colour of the sky?")
    return older, host.stored_session.path


async def test_renaming_the_open_conversation_keeps_its_place_and_survives_a_turn(
    rig: Rig, fake_model: FakeModel
) -> None:
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Hi."]), FakeTurn.text(["Again."]))
    await _chat(client, "Hello there")
    path = host.stored_session.path
    updated_before = _meta(path)["updated_at"]

    renamed = await client.sessions_rename(
        host.stored_session.meta.id, "  Trip   plans "
    )

    assert renamed.current is not None and renamed.current.title == "Trip plans"
    assert [item.title for item in renamed.items] == ["Trip plans"]
    assert _meta(path)["title"] == "Trip plans"
    assert _meta(path)["updated_at"] == updated_before
    await _chat(client, "More")
    assert _meta(path)["title"] == "Trip plans"


async def test_renaming_a_saved_conversation_leaves_the_open_one_alone(
    rig: Rig, fake_model: FakeModel
) -> None:
    host, client = rig
    older, active = await _two_conversations(rig, fake_model)
    older_id = older.stem

    await client.sessions_rename(older_id, "France")

    assert _meta(older)["title"] == "France"
    assert host.stored_session.path == active
    assert host.stored_session.meta.title == "Colour of the sky?"


def test_a_title_loses_control_and_bidi_characters() -> None:
    renamed = SessionsRenameRequest(
        session_id="x", title="Trip\u202e\x1b[31m plan\u2066s"
    )
    assert renamed.title == "Trip[31m plans"


def test_a_title_is_one_line_capped_and_never_blank() -> None:
    long = SessionsRenameRequest(session_id="x", title="a\nb " + "c" * 200)
    assert "\n" not in long.title
    assert len(long.title) == MAX_SESSION_TITLE_CHARS
    with pytest.raises(ValidationError):
        SessionsRenameRequest(session_id="x", title=" \n\t ")


async def test_pinning_is_saved_and_listed_then_undone(
    rig: Rig, fake_model: FakeModel
) -> None:
    _host, client = rig
    older, _active = await _two_conversations(rig, fake_model)

    pinned = await client.sessions_pin(older.stem, True)

    assert {item.id: item.pinned for item in pinned.items}[older.stem] is True
    assert _meta(older)["pinned"] is True
    listing = await client.sessions_list()
    assert {item.id: item.pinned for item in listing.items}[older.stem] is True

    unpinned = await client.sessions_pin(older.stem, False)
    assert {item.id: item.pinned for item in unpinned.items}[older.stem] is False


async def test_a_file_without_pinned_reads_as_unpinned(aldwin_home: Path) -> None:
    store = SessionStore(aldwin_home / "sessions")
    stored = store.create_new()
    stored.turns = [{"text": "Hi", "skills": []}]
    store.save(stored, stored.memory, interrupted=False)
    raw = json.loads(stored.path.read_text("utf-8"))
    del raw["meta"]["pinned"]
    stored.path.write_text(json.dumps(raw), "utf-8")

    assert [meta.pinned for meta in store.list_metas()] == [False]
    loaded = store.load_id(stored.meta.id)
    assert loaded is not None and loaded.meta.pinned is False


async def test_deleting_a_saved_conversation_removes_its_file_only(
    rig: Rig, fake_model: FakeModel
) -> None:
    host, client = rig
    older, active = await _two_conversations(rig, fake_model)

    after = await client.sessions_delete(older.stem)

    assert not older.exists()
    assert active.exists()
    assert [item.id for item in after.items] == [active.stem]
    assert host.stored_session.path == active


async def test_deleting_the_open_conversation_starts_a_new_chat_and_never_rewrites_it(
    rig: Rig, fake_model: FakeModel
) -> None:
    host, client = rig
    older, active = await _two_conversations(rig, fake_model)

    after = await client.sessions_delete(active.stem)

    assert not active.exists()
    assert after.current is not None and after.current.id not in {
        active.stem,
        older.stem,
    }
    assert [item.id for item in after.items] == [older.stem]
    history = await client.history()
    assert history.messages == []
    await client.sessions_open(older.stem)
    assert not active.exists()


async def test_deleting_or_unknown_ids_say_not_found(rig: Rig) -> None:
    _host, client = rig
    for call in (
        client.sessions_delete("2026-01-01_deadbeef"),
        client.sessions_rename("2026-01-01_deadbeef", "x"),
        client.sessions_pin("../escape", True),
    ):
        with pytest.raises(ProtocolError) as caught:
            await call
        assert caught.value.code == "not_found"


async def test_the_open_conversation_cannot_be_deleted_mid_turn_but_can_be_renamed(
    rig: Rig, fake_model: FakeModel
) -> None:
    host, client = rig
    hang = FakeTurn.hang(prefix_chunks=["Working"])
    fake_model.add_turns(hang)
    turn = asyncio.create_task(_chat(client, "long job"))
    await asyncio.wait_for(hang.hang_started.wait(), timeout=2.0)
    session_id = host.stored_session.meta.id

    with pytest.raises(ProtocolError) as caught:
        await client.sessions_delete(session_id)
    assert caught.value.code == "busy"
    assert host.stored_session.path.exists()

    renamed = await client.sessions_rename(session_id, "Long job")
    assert renamed.current is not None and renamed.current.title == "Long job"

    await client.stop()
    await turn
    assert _meta(host.stored_session.path)["title"] == "Long job"


async def test_a_conversation_another_window_is_answering_is_left_alone(
    rig: Rig, fake_model: FakeModel, aldwin_home: Path
) -> None:
    _host, client = rig
    older, _active = await _two_conversations(rig, fake_model)
    other_window = ConversationLock(aldwin_home, older.stem)
    assert other_window.try_acquire()
    try:
        for call in (
            client.sessions_delete(older.stem),
            client.sessions_rename(older.stem, "Mine"),
        ):
            with pytest.raises(ProtocolError) as caught:
                await call
            assert caught.value.code == "busy"
    finally:
        other_window.release()
    assert older.exists()
    assert _meta(older)["title"] == "Capital of France?"


async def test_a_conversation_deleted_in_another_window_is_never_written_back(
    rig: Rig, fake_model: FakeModel
) -> None:
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["First."]), FakeTurn.text(["Second."]))
    await _chat(client, "Hello")
    gone = host.stored_session.path
    gone.unlink()  # another window deleted it

    events = [e async for e in client.turn("Are you there?")]

    assert not gone.exists()
    assert host.stored_session.path != gone
    notices = [e.message for e in events if isinstance(e, NoticeEvent)]
    assert notices == [DELETED_ELSEWHERE]


async def test_renaming_a_conversation_deleted_in_another_window_says_not_found(
    rig: Rig, fake_model: FakeModel
) -> None:
    host, client = rig
    fake_model.add_turns(FakeTurn.text(["Hi."]))
    await _chat(client, "Hello")
    session_id = host.stored_session.meta.id
    host.stored_session.path.unlink()

    with pytest.raises(ProtocolError) as caught:
        await client.sessions_rename(session_id, "New name")

    assert caught.value.code == "not_found"
    assert not (host.stored_session.path.parent / f"{session_id}.json").exists()
