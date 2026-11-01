"""A token pasted into chat: the host warns, then keeps it out of saved sessions
and out of the conversation the model sees on later turns."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import collect
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.host import runner
from aldwin.host.runner import PASTED_SECRET_NOTICE
from aldwin.protocol import REDACTED, DoneEvent, NoticeEvent, TextEvent

TOKEN = "ghp_" + "a1B2" * 9
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlLXZhbHVl"


def _session_files(home: Path) -> list[Path]:
    return sorted((home / "sessions").glob("*.json"))


def _sessions_text(home: Path) -> str:
    """Saved sessions decoded, so escaped text compares as typed."""
    texts = []
    for path in _session_files(home):
        saved = json.loads(path.read_text("utf-8"))
        texts.append(json.dumps(saved, ensure_ascii=False) + saved["memory"])
    return "".join(texts)


def _model_saw(fake_model: FakeModel, text: str) -> bool:
    return any(text in (message.content or "") for message in fake_model.calls[-1])


async def _paste(client: AldwinClient, fake_model: FakeModel) -> list[Any]:
    fake_model.add_turns(FakeTurn.text(["Got it."]))
    return await collect(client.turn(f"here is my token {TOKEN}"))


async def test_notice_comes_first_and_message_is_unchanged(
    host_client: AldwinClient, fake_model: FakeModel, aldwin_home: Path
) -> None:
    events = await _paste(host_client, fake_model)

    notices = [i for i, ev in enumerate(events) if isinstance(ev, NoticeEvent)]
    texts = [i for i, ev in enumerate(events) if isinstance(ev, TextEvent)]
    assert [events[i].message for i in notices] == [PASTED_SECRET_NOTICE]
    assert notices[0] < texts[0]
    assert _model_saw(fake_model, f"here is my token {TOKEN}")
    credentials = aldwin_home / "credentials"
    assert not any(path.is_file() for path in credentials.rglob("*"))


async def test_saved_session_is_redacted(
    host_client: AldwinClient, fake_model: FakeModel, aldwin_home: Path
) -> None:
    await _paste(host_client, fake_model)

    [path] = _session_files(aldwin_home)
    saved = json.loads(path.read_text("utf-8"))
    assert TOKEN not in _sessions_text(aldwin_home)
    assert REDACTED in json.loads(saved["memory"])["steps"][0]["data"]["task"]
    assert saved["turns"][-1]["text"] == f"here is my token {REDACTED}"
    assert saved["turns"][-1]["text"].count(REDACTED) == 1
    assert saved["meta"]["title"] == f"here is my token {REDACTED}"


async def test_next_turn_memory_is_redacted(
    host_client: AldwinClient, fake_model: FakeModel
) -> None:
    await _paste(host_client, fake_model)
    fake_model.add_turns(FakeTurn.text(["Sure."]))

    await collect(host_client.turn("what did I just send?"))

    assert not _model_saw(fake_model, TOKEN)
    assert _model_saw(fake_model, REDACTED)


async def test_taint_survives_later_saves(
    host_client: AldwinClient,
    fake_model: FakeModel,
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_restore(*_: Any) -> Any:
        raise RuntimeError("restore failed")

    # With the live swap broken the token stays in memory, so only the taint
    # keeps it off disk on later saves.
    monkeypatch.setattr(runner, "restore_session", broken_restore)
    await _paste(host_client, fake_model)
    fake_model.add_turns(FakeTurn.text(["One."]), FakeTurn.text(["Two."]))

    await collect(host_client.turn("first follow-up"))
    await collect(host_client.turn("second follow-up"))

    assert _model_saw(fake_model, TOKEN)
    assert TOKEN not in _sessions_text(aldwin_home)


async def test_new_session_starts_clean(
    host_client: AldwinClient, fake_model: FakeModel, aldwin_home: Path
) -> None:
    await _paste(host_client, fake_model)
    [old] = _session_files(aldwin_home)
    example = "sk-proj-" + "Example1" * 3
    fake_model.add_turns(FakeTurn.text([f"An example key looks like {example}."]))

    await host_client.new_conversation()
    await collect(host_client.turn("show me what an API key looks like"))

    [new] = [path for path in _session_files(aldwin_home) if path != old]
    assert example in json.loads(new.read_text("utf-8"))["memory"]
    assert TOKEN not in _sessions_text(aldwin_home)


async def test_jwt_warns_and_runs(
    host_client: AldwinClient, fake_model: FakeModel, aldwin_home: Path
) -> None:
    fake_model.add_turns(FakeTurn.text(["That token expires tomorrow."]))

    events = await collect(host_client.turn(f"why is this JWT rejected? {JWT}"))

    assert PASTED_SECRET_NOTICE in [
        ev.message for ev in events if isinstance(ev, NoticeEvent)
    ]
    assert isinstance(events[-1], DoneEvent) and events[-1].reason == "answered"
    text = _sessions_text(aldwin_home)
    assert JWT[:30] not in text
    assert REDACTED in text
