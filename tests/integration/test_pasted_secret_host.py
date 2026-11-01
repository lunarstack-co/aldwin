"""A token pasted into chat: the host warns and redacts it before the model
call, so neither the model nor the saved session ever holds it."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import collect
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.host.runner import PASTED_SECRET_NOTICE
from aldwin.protocol import REDACTED, DoneEvent, Event, NoticeEvent, TextEvent

TOKEN = "ghp_" + "a1B2" * 9
SLACK = "xoxb-1234567890-abcdefghij"
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
    return any(
        text in (message.content or "") for call in fake_model.calls for message in call
    )


def _notices(events: list[Event]) -> list[str]:
    return [ev.message for ev in events if isinstance(ev, NoticeEvent)]


async def test_notice_comes_first_and_model_gets_redacted_text(
    host_client: AldwinClient, fake_model: FakeModel, aldwin_home: Path
) -> None:
    fake_model.add_turns(FakeTurn.text(["Got it."]))

    events = await collect(host_client.turn(f"connect GitHub, my token is {TOKEN}"))

    first_notice = next(i for i, ev in enumerate(events) if isinstance(ev, NoticeEvent))
    first_text = next(i for i, ev in enumerate(events) if isinstance(ev, TextEvent))
    assert _notices(events) == [PASTED_SECRET_NOTICE]
    assert first_notice < first_text
    assert _model_saw(fake_model, f"connect GitHub, my token is {REDACTED}")
    assert not _model_saw(fake_model, TOKEN)
    credentials = aldwin_home / "credentials"
    assert not any(path.is_file() for path in credentials.rglob("*"))


async def test_saved_session_never_holds_the_token(
    host_client: AldwinClient, fake_model: FakeModel, aldwin_home: Path
) -> None:
    fake_model.add_turns(FakeTurn.text(["Got it."]), FakeTurn.text(["Sure."]))

    await collect(host_client.turn(f"here is my token {TOKEN}"))
    await collect(host_client.turn("what did I just send?"))

    [path] = _session_files(aldwin_home)
    saved = json.loads(path.read_text("utf-8"))
    assert TOKEN not in _sessions_text(aldwin_home)
    assert saved["turns"][0]["text"] == f"here is my token {REDACTED}"
    assert saved["meta"]["title"] == f"here is my token {REDACTED}"
    assert REDACTED in json.loads(saved["memory"])["steps"][0]["data"]["task"]
    assert not _model_saw(fake_model, TOKEN)


async def test_two_tokens_give_one_notice_and_keep_the_rest(
    host_client: AldwinClient, fake_model: FakeModel
) -> None:
    fake_model.add_turns(FakeTurn.text(["Both noted."]))

    events = await collect(host_client.turn(f"rotate {TOKEN} and {SLACK} for me"))

    assert _notices(events) == [PASTED_SECRET_NOTICE]
    assert _model_saw(fake_model, f"rotate {REDACTED} and {REDACTED} for me")


async def test_plain_message_gets_no_notice(
    host_client: AldwinClient, fake_model: FakeModel
) -> None:
    fake_model.add_turns(FakeTurn.text(["Tuesday."]))

    events = await collect(
        host_client.turn("what day is it? task-1234567890abcdefghij")
    )

    assert _notices(events) == []
    assert _model_saw(fake_model, "task-1234567890abcdefghij")


async def test_jwt_warns_and_runs(
    host_client: AldwinClient, fake_model: FakeModel, aldwin_home: Path
) -> None:
    fake_model.add_turns(FakeTurn.text(["I can't see the token itself."]))

    events = await collect(host_client.turn(f"why is this JWT rejected? {JWT}"))

    assert PASTED_SECRET_NOTICE in _notices(events)
    assert isinstance(events[-1], DoneEvent) and events[-1].reason == "answered"
    assert not _model_saw(fake_model, JWT)
    text = _sessions_text(aldwin_home)
    assert JWT[:30] not in text
    assert REDACTED in text
