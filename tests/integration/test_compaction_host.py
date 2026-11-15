"""Host-level tests for making room in long conversations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from agentling import ChatMessage, ModelContextLengthError, Usage
from agentling.memory import ActionStep, FinalStep, Step, TaskStep, ToolResult
from canary import CANARY
from conftest import collect
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core import compaction as c
from aldwin.core.agent import build_agent
from aldwin.core.home import atomic_write
from aldwin.core.memory_index import default_memory_index
from aldwin.core.policy import wrap_untrusted
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.host.compaction import Room, Sizer, make_room
from aldwin.protocol import (
    DoneEvent,
    ErrorEvent,
    Event,
    HistoryEvent,
    NoticeEvent,
    StatusEvent,
    ToolResultEvent,
)

SUMMARISED = c.MESSAGES["summarised"]
FALLBACK = c.MESSAGES["fallback"]
SHORTENED = c.MESSAGES["shortened"]
MAKING = c.MESSAGES["making"]


@dataclass
class Rig:
    """A running host, its client and the fake model behind it."""

    host: AldwinHost
    client: AldwinClient
    model: FakeModel
    home: Path

    @property
    def fixed(self) -> int:
        return self.host._sizer(None).fixed

    def window(self, tokens: int) -> None:
        self.model.context_window = tokens  # type: ignore[attr-defined]

    def state(self) -> c.CompactionState:
        return self.host._compaction_state()

    def over_limit(self, text: str) -> bool:
        room = self.host._room()
        assert room is not None
        return self.host._sizer(text).size(self.state()) > room.limit


RigFactory = Callable[..., Awaitable[Rig]]


@pytest.fixture
async def make_rig(aldwin_home: Path) -> AsyncIterator[RigFactory]:
    """Start hosts on fake models; closes every one at the end of the test."""
    started: list[Rig] = []

    async def start(
        *,
        percent: int | None = None,
        model_name: str | None = None,
        workspace: Path | None = None,
    ) -> Rig:
        if percent is not None:
            atomic_write(
                aldwin_home / "config.yaml",
                f"limits:\n  context_compact_percent: {percent}\n",
            )
        model = FakeModel()

        def builder(name: str, ask: AskFn) -> Any:
            return build_agent(
                AldwinConfig(model=name),
                SecretsConfig(),
                ask,
                home=aldwin_home,
                model=model,
                workspace=workspace,
            )

        transport = InProcessTransport()
        kwargs: dict[str, Any] = {"agent_builder": builder}
        if model_name is not None:
            kwargs["initial_model"] = model_name
        host = AldwinHost(transport, **kwargs)
        await host.start()
        client = AldwinClient(transport.connect())
        await client.connect()
        rig = Rig(host=host, client=client, model=model, home=aldwin_home)
        started.append(rig)
        return rig

    yield start
    for rig in started:
        await rig.client.close()
        await rig.host.stop_host()


@pytest.fixture
async def rig(make_rig: RigFactory) -> Rig:
    return await make_rig()


def _answer(text: str = "noted") -> FakeTurn:
    return FakeTurn(chunks=[text], usage=None)


async def _say(rig: Rig, text: str) -> list[Event]:
    return await collect(rig.client.turn(text))


async def _say_answered(rig: Rig, text: str, answer: str = "noted") -> list[Event]:
    """Send a turn, scripting a summary first if room will be made."""
    if rig.host._room() is not None and rig.over_limit(text):
        rig.model.add_turns(FakeTurn.summary())
    rig.model.add_turns(_answer(answer))
    return await _say(rig, text)


def _task_texts(rig: Rig) -> list[str]:
    steps = rig.host._agent_session.memory.steps
    return [step.task for step in steps if isinstance(step, TaskStep)]


def _notices(events: list[Event]) -> list[str]:
    return [ev.message for ev in events if isinstance(ev, NoticeEvent)]


def _turn_text(index: int, chars: int = 3_500) -> str:
    head = f"turn {index} "
    return head + "w" * (chars - len(head))


def _stored(rig: Rig) -> dict[str, Any]:
    raw = json.loads(rig.host._stored_session.path.read_text("utf-8"))
    assert isinstance(raw, dict)
    return raw


def _summary_calls(rig: Rig) -> list[list[ChatMessage]]:
    return [
        call
        for call in rig.model.calls
        if call and "material to summarise" in call[0].content
    ]


# Retiring the old behaviour


async def test_overflow_keeps_every_message(rig: Rig) -> None:
    """A provider refusal never removes a message from the chat, search or turns."""
    for word in ["alpha", "bravo", "charlie", "delta", "echo"]:
        rig.model.add_turns(FakeTurn.text(f"noted {word}"))
        events = await _say(rig, f"remember the word {word}")
        assert isinstance(events[-1], DoneEvent)

    rig.model.add_turns(
        FakeTurn.error(ModelContextLengthError("too long")),
        FakeTurn.text("still here"),
    )
    events = await _say(rig, "what was the first word?")
    assert isinstance(events[-1], DoneEvent)

    history = await rig.client.history(limit=500)
    assert isinstance(history, HistoryEvent)
    user_rows = [item.text for item in history.messages if item.role == "user"]
    assert user_rows[0] == "remember the word alpha"
    assert len(user_rows) == 6

    stored_turns = rig.host._stored_session.turns or []
    assert [turn["text"] for turn in stored_turns] == _task_texts(rig)

    found = default_memory_index(rig.home).search("alpha", scope="conversations")
    assert "remember the word alpha" in found


async def test_no_twenty_exchange_cap(rig: Rig) -> None:
    """With room to spare, every earlier message reaches the model."""
    rig.window(200_000)
    events: list[Event] = []
    for i in range(30):
        rig.model.add_turns(_answer(f"a{i}"))
        events += await _say(rig, f"short message {i}")

    users = [m for m in rig.model.calls[-1] if m.role == "user"]
    assert len(users) == 30
    assert _notices(events) == []
    assert rig.host._stored_session.compaction is None


async def test_three_refusals_end_the_turn_without_losing_steps(rig: Rig) -> None:
    """Two fallbacks after refusals, then the turn ends with a clear message."""
    for i in range(5):
        rig.model.add_turns(FakeTurn.text(f"a{i}"))
        await _say(rig, f"q{i}")
    before = len(rig.host._agent_session.memory.steps)
    rig.model.add_turns(
        *(FakeTurn.error(ModelContextLengthError("too long")) for _ in range(3))
    )

    events = await _say(rig, "one more")

    assert _notices(events) == [FALLBACK, FALLBACK]
    assert all(ev.topic == "room" for ev in events if isinstance(ev, NoticeEvent))
    error = events[-1]
    assert isinstance(error, ErrorEvent)
    assert error.code == "request_rejected"
    assert error.message.endswith(
        "even after making room. Start a new chat or choose a model with more room."
    )
    assert len(rig.host._agent_session.memory.steps) == before + 1
    assert _summary_calls(rig) == []


# Making room before a turn


async def test_room_is_made_when_a_small_chat_passes_the_ceiling() -> None:
    """At 80% the line can sit on the ceiling, where a small chat can still overflow."""
    steps: list[Step] = [TaskStep(task="x" * 7_000), FinalStep(answer="ok")]
    conversation = [message for step in steps for message in step.to_messages()]
    conversation.append(ChatMessage(role="user", content="next"))
    room = Room(
        window=16_384,
        limit=c.compaction_limit(16_384, None, 80),
        ceiling=c.hard_ceiling(16_384, None),
    )
    sizer = Sizer(fixed=11_000, conversation=conversation, steps=steps)

    async def summarise(*_: object) -> tuple[str, str | None, Usage | None]:
        return "\n".join(f"## {heading}\nNone" for heading in c.HEADINGS), "stop", None

    async def on_status(_: str) -> None:
        return None

    outcome = await make_room(
        steps=steps,
        state=c.CompactionState(),
        sizer=sizer,
        room=room,
        task_tokens=2,
        summarise=summarise,
        now=lambda: datetime.now(UTC),
        on_status=on_status,
        model_name="m",
    )

    assert outcome.notice == "summarised"
    assert sizer.size(outcome.state) <= room.ceiling


async def test_shortening_alone_is_enough(rig: Rig) -> None:
    """Old tool results are shortened first; no summary, and the user is told."""
    rig.window(rig.fixed * 3)
    memory = rig.host._agent_session.memory
    big = wrap_untrusted("r" * 20_000, tool="read_file", target="notes.md")
    message = ChatMessage(role="assistant", content="", tool_calls=[])
    memory.add(TaskStep(task="read my notes"))
    memory.add(
        ActionStep(
            model_message=message,
            tool_results=[ToolResult(tool_call_id="1", name="read_file", content=big)],
        )
    )
    memory.add(FinalStep(answer="read them"))
    rig.model.add_turns(_answer())

    events = await _say(rig, "thanks")

    assert isinstance(events[-1], DoneEvent)
    statuses = [ev.message for ev in events if isinstance(ev, StatusEvent)]
    assert statuses[:2] == [MAKING, ""]
    assert _notices(events) == [SHORTENED]
    assert _summary_calls(rig) == []
    assert rig.state().shortened_through == 3
    sent_tool = [m for m in rig.model.calls[-1] if m.role == "tool"]
    assert "[Shortened:" in sent_tool[0].content


async def test_model_too_small(rig: Rig) -> None:
    rig.window(4_096)
    events = await _say(rig, "hi")
    error = events[-1]
    assert isinstance(error, ErrorEvent)
    assert error.code == "request_rejected"
    assert error.message == (
        "gpt-4o has too little room to work with Aldwin. Choose a model with "
        "more room in Settings."
    )
    assert rig.model.calls == []


async def test_one_message_too_long(rig: Rig) -> None:
    rig.window(rig.fixed * 3)
    events = await _say(rig, "x" * 100_000)
    error = events[-1]
    assert isinstance(error, ErrorEvent)
    assert error.message == (
        "That message is too long for gpt-4o. Try a shorter one, or choose a "
        "model with more room."
    )
    assert rig.model.calls == []
    assert len(rig.host._stored_session.turns or []) == len(_task_texts(rig))


async def test_very_small_model_still_runs(rig: Rig) -> None:
    """A model whose fixed part is past the line runs with fallback only."""
    fixed = rig.fixed
    rig.window(int(fixed / 0.68))
    room = rig.host._room()
    assert room is not None
    assert room.limit < fixed < room.ceiling

    events: list[Event] = []
    for i in range(4):
        rig.model.add_turns(_answer(f"a{i}"))
        turn_events = await _say(rig, _turn_text(i, 1_000))
        assert isinstance(turn_events[-1], DoneEvent)
        events += turn_events

    assert _summary_calls(rig) == []
    assert FALLBACK in _notices(events)
    assert rig.state().left_out > 0


async def test_did_not_help(rig: Rig) -> None:
    """A stored summary too big for the window ends the turn after one round."""
    rig.window(rig.fixed * 3)
    rig.model.add_turns(_answer())
    await _say(rig, "first")
    steps = rig.host._agent_session.memory.steps
    huge = "\n".join(f"## {h}\n" + "z" * 4_000 for h in c.HEADINGS)
    rig.host._stored_session.compaction = c.CompactionState(
        summary=huge, cut=len(steps)
    ).to_dict()

    events = await _say(rig, "second")

    error = events[-1]
    assert isinstance(error, ErrorEvent)
    assert "even after making room" in error.message
    assert _summary_calls(rig) == []


async def test_real_count_drives_the_decision(rig: Rig) -> None:
    """A provider count over the limit makes room even when the estimate is small."""
    rig.window(rig.fixed * 3)
    room = rig.host._room()
    assert room is not None
    rig.model.add_turns(
        FakeTurn(
            chunks=["ok"], usage=Usage(input_tokens=room.limit + 500, output_tokens=5)
        )
    )
    await _say(rig, "first")
    rig.model.add_turns(_answer())

    events = await _say(rig, "second")

    assert isinstance(events[-1], DoneEvent)
    assert not rig.state().is_empty


async def test_no_usage_means_estimate_only(rig: Rig) -> None:
    """Without provider counts, room is made on the first turn the estimate is over."""
    rig.window(rig.fixed * 3)
    turn = 0
    while not rig.over_limit(_turn_text(turn)):
        rig.model.add_turns(_answer())
        events = await _say(rig, _turn_text(turn))
        assert _notices(events) == []
        turn += 1

    events = await _say_answered(rig, _turn_text(turn))

    assert _notices(events) == [SUMMARISED]


# The summary


async def _fill_until_summary(rig: Rig, start: int = 0) -> tuple[int, list[Event]]:
    turn = start
    while True:
        events = await _say_answered(rig, _turn_text(turn))
        turn += 1
        if SUMMARISED in _notices(events) or FALLBACK in _notices(events):
            return turn, events


async def test_first_summary(rig: Rig) -> None:
    rig.window(rig.fixed * 3)
    turns, events = await _fill_until_summary(rig)

    kinds = [
        (type(ev).__name__, getattr(ev, "message", ""))
        for ev in events
        if isinstance(ev, StatusEvent | NoticeEvent)
    ]
    assert kinds[:4] == [
        ("StatusEvent", MAKING),
        ("StatusEvent", "Writing notes on earlier messages to make room..."),
        ("StatusEvent", ""),
        ("NoticeEvent", SUMMARISED),
    ]
    notices = [ev for ev in events if isinstance(ev, NoticeEvent)]
    assert notices and all(ev.topic == "room" for ev in notices)
    assert isinstance(events[-1], DoneEvent)
    state = rig.state()
    assert state.summary is not None
    assert all(h in state.summary for h in c.HEADINGS)
    assert isinstance(rig.host._agent_session.memory.steps[state.cut], TaskStep)
    history = await rig.client.history(limit=500)
    assert isinstance(history, HistoryEvent)
    assert len([i for i in history.messages if i.role == "user"]) == turns

    summary_call = _summary_calls(rig)[0]
    index = rig.model.calls.index(summary_call)
    assert rig.model.tool_specs[index] == []
    assert "[User]: turn 0 " in summary_call[1].content
    sent = rig.model.calls[-1]
    first_user = next(m for m in sent if m.role == "user")
    assert first_user.content.startswith(c.SUMMARY_PREAMBLE)


async def test_second_summary_updates_the_first(rig: Rig) -> None:
    rig.window(rig.fixed * 3)
    turns, _ = await _fill_until_summary(rig)
    first_cut = rig.state().cut

    await _fill_until_summary(rig, turns)

    assert rig.state().cut > first_cut
    second = _summary_calls(rig)[-1]
    assert "<previous_notes>" in second[1].content


async def test_cut_off_summary_falls_back_and_waits(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.window(rig.fixed * 3)
    turn = 0
    while not rig.over_limit(_turn_text(turn)):
        rig.model.add_turns(_answer())
        await _say(rig, _turn_text(turn))
        turn += 1
    rig.model.add_turns(FakeTurn.summary(finish_reason="length"), _answer())

    before = datetime.now(UTC)
    events = await _say(rig, _turn_text(turn))

    assert _notices(events) == [FALLBACK]
    state = rig.state()
    assert state.summary is None
    assert state.left_out > 0
    assert state.failures == 1
    assert state.retry_after is not None
    wait = datetime.fromisoformat(state.retry_after) - before
    assert timedelta(seconds=55) < wait < timedelta(seconds=65)

    calls_before = len(_summary_calls(rig))
    turn += 1
    while not rig.over_limit(_turn_text(turn)):
        rig.model.add_turns(_answer())
        await _say(rig, _turn_text(turn))
        turn += 1
    rig.model.add_turns(_answer())
    events = await _say(rig, _turn_text(turn))
    assert len(_summary_calls(rig)) == calls_before
    assert FALLBACK in _notices(events)


async def test_summary_timeout_falls_back(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.window(rig.fixed * 3)
    monkeypatch.setattr(c, "summary_timeout", lambda *, local: 0.05)
    turn = 0
    while not rig.over_limit(_turn_text(turn)):
        rig.model.add_turns(_answer())
        await _say(rig, _turn_text(turn))
        turn += 1
    rig.model.add_turns(FakeTurn.hang(), _answer())

    events = await _say(rig, _turn_text(turn))

    assert isinstance(events[-1], DoneEvent)
    assert _notices(events) == [FALLBACK]
    assert rig.state().failures == 1


async def test_summary_usage_counts_toward_the_turn_only(rig: Rig) -> None:
    rig.window(rig.fixed * 3)
    turn = 0
    while not rig.over_limit(_turn_text(turn)):
        rig.model.add_turns(_answer())
        await _say(rig, _turn_text(turn))
        turn += 1
    rig.model.add_turns(
        FakeTurn.summary(usage=Usage(input_tokens=8_000, output_tokens=900)),
        FakeTurn(chunks=["ok"], usage=Usage(input_tokens=1_000, output_tokens=100)),
    )

    events = await _say(rig, _turn_text(turn))

    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.usage is not None
    assert (done.usage.input_tokens, done.usage.output_tokens) == (9_000, 1_000)
    assert done.context is not None
    assert done.context.used == 1_000


async def test_secrets_never_reach_the_stored_summary(rig: Rig) -> None:
    rig.window(rig.fixed * 3)
    turn = 0
    while not rig.over_limit(_turn_text(turn)):
        rig.model.add_turns(_answer())
        await _say(rig, _turn_text(turn))
        turn += 1
    leaky = "\n".join(f"## {h}\nkey {CANARY}" for h in c.HEADINGS)
    rig.model.add_turns(FakeTurn.summary(leaky), _answer())

    await _say(rig, _turn_text(turn))

    entry = _stored(rig)["compaction"]
    assert CANARY not in json.dumps(entry)
    assert "‹redacted›" in entry["summary"]


async def test_stop_during_the_summary_keeps_the_old_state(rig: Rig) -> None:
    rig.window(rig.fixed * 3)
    turn = 0
    while not rig.over_limit(_turn_text(turn)):
        rig.model.add_turns(_answer())
        await _say(rig, _turn_text(turn))
        turn += 1
    hang = FakeTurn.hang()
    rig.model.add_turns(hang)
    steps_before = len(rig.host._agent_session.memory.steps)

    task = asyncio.create_task(_say(rig, _turn_text(turn)))
    await asyncio.wait_for(hang.hang_started.wait(), timeout=2.0)
    await rig.client.stop()
    events = await asyncio.wait_for(task, timeout=2.0)

    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.reason == "stopped"
    assert "compaction" not in _stored(rig)
    assert len(rig.host._agent_session.memory.steps) == steps_before


async def test_switch_to_a_smaller_model(make_rig: RigFactory) -> None:
    rig = await make_rig()
    rig.window(200_000)
    for i in range(8):
        rig.model.add_turns(_answer())
        await _say(rig, _turn_text(i))
    assert rig.host._stored_session.compaction is None

    result = await rig.client.set_model("openai/gpt-4o-mini")
    assert isinstance(result, DoneEvent)
    rig.window(rig.fixed * 3)
    rig.model.add_turns(FakeTurn.summary(), _answer())

    events = await _say(rig, "and now?")

    assert _notices(events) == [
        "This chat is longer than gpt-4o-mini can hold, so Aldwin made room. "
        "Your messages are all still here."
    ]


async def test_lower_setting_makes_room_earlier(make_rig: RigFactory) -> None:
    async def first_room_turn(percent: int) -> int:
        rig = await make_rig(percent=percent)
        await rig.client.new_conversation()
        rig.window(rig.fixed * 10)
        turns, _ = await _fill_until_summary(rig)
        return turns

    assert await first_room_turn(60) < await first_room_turn(80)


# State on disk


async def test_bad_stored_cut_is_ignored(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    rig.model.add_turns(_answer())
    await _say(rig, "first")
    rig.host._stored_session.compaction = c.CompactionState(
        summary="s", cut=1
    ).to_dict()
    rig.model.add_turns(_answer("second answer"))

    events = await _say(rig, "second")

    assert isinstance(events[-1], DoneEvent)
    assert "ignoring invalid compaction state" in caplog.text
    sent_users = [m.content for m in rig.model.calls[-1] if m.role == "user"]
    assert sent_users == ["first", "second"]


async def test_conversation_json_hides_the_notes(rig: Rig) -> None:
    from aldwin.host.home_api import load_conversation_json

    rig.window(rig.fixed * 3)
    await _fill_until_summary(rig)
    assert "compaction" in _stored(rig)

    body = load_conversation_json(rig.host._stored_session.meta.id, rig.home)

    assert body is not None
    assert "compaction" not in body


async def test_recall_finds_a_message_the_model_no_longer_sees(rig: Rig) -> None:
    """After a summary, recall reads the original words from this conversation."""
    rig.window(rig.fixed * 3)
    rig.model.add_turns(_answer())
    await _say(rig, "the booking says free cancellation until 10 May")
    await _fill_until_summary(rig, 1)
    first = rig.host._agent_session.memory.steps[0]
    assert isinstance(first, TaskStep)
    assert rig.state().cut > 0

    rig.model.add_turns(
        FakeTurn.tool_call(
            "recall", {"query": "cancellation", "scope": "this_conversation"}
        ),
        _answer("10 May"),
    )
    events = await _say(rig, "when can I cancel?")

    results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
    assert results and results[0].ok
    tool_message = next(m for m in rig.model.calls[-1] if m.role == "tool")
    assert "free cancellation until 10 May" in tool_message.content
    assert "[turn 1 · user]" in tool_message.content
    assert tool_message.content.count("<untrusted id=") == 1


# The context ring after a reload


async def test_history_has_no_ring_for_an_empty_chat(rig: Rig) -> None:
    rig.window(100_000)
    assert (await rig.client.history()).context is None


async def test_the_ring_comes_back_with_the_history(rig: Rig) -> None:
    """A reloaded page gets the same numbers the last answer reported."""
    rig.window(100_000)
    # The answer reports no usage, so the host measures the chat itself.
    events = await _say_answered(rig, "hello there")
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.context is not None

    history = await rig.client.history()

    assert history.context is not None
    assert history.context.limit == 100_000
    room = rig.host._room()
    assert room is not None and history.context.compact_at == room.limit == 60_000
    assert history.context.used == done.context.used > rig.fixed
    assert {s.key for s in history.context.segments} >= {"tools"}


async def test_the_ring_reads_the_same_after_reopening_and_restarting(
    make_rig: RigFactory,
) -> None:
    """The provider's count is saved with the chat, so the ring does not jump later."""
    rig = await make_rig()
    rig.window(100_000)
    rig.model.add_turns(
        FakeTurn(chunks=["ok"], usage=Usage(input_tokens=9_000, output_tokens=5))
    )
    done = (await _say(rig, "hello there"))[-1]
    assert isinstance(done, DoneEvent) and done.context is not None
    assert done.context.used == 9_000
    assert _stored(rig)["measured"]["tokens"] == 9_000

    reopened = await rig.client.history()
    assert reopened.context is not None
    assert abs(reopened.context.used - done.context.used) <= 10

    restarted = await make_rig()
    restarted.window(100_000)
    history = await restarted.client.history()
    assert history.context is not None
    assert abs(history.context.used - done.context.used) <= 10


async def test_a_count_is_not_reused_for_another_model_or_view(rig: Rig) -> None:
    rig.window(100_000)
    rig.model.add_turns(
        FakeTurn(chunks=["ok"], usage=Usage(input_tokens=9_000, output_tokens=5))
    )
    await _say(rig, "hello there")
    assert rig.host._real_offset() != 0
    rig.host._stored_session.compaction = {"version": 1, "cut": 0}
    assert rig.host._real_offset() == 0


async def test_switching_model_reports_the_ring(rig: Rig) -> None:
    rig.window(100_000)
    await _say_answered(rig, "hello there")

    result = await rig.client.set_model(rig.host._model)

    assert isinstance(result, DoneEvent)
    assert result.context is not None and result.context.limit == 100_000


async def test_unknown_window_has_no_ring(rig: Rig) -> None:
    await _say_answered(rig, "hello there")
    assert (await rig.client.history()).context is None


# Making room by hand


async def test_make_room_now_in_a_short_chat(rig: Rig) -> None:
    rig.window(200_000)
    for i in range(2):
        rig.model.add_turns(_answer())
        await _say(rig, f"q{i}")

    result, notices = await rig.client.compact()

    assert isinstance(result, ErrorEvent)
    assert result.code == "invalid_request"
    assert result.message == c.MESSAGES["still_short"]
    assert notices == []
    assert _summary_calls(rig) == []


async def test_make_room_now_while_busy(rig: Rig) -> None:
    hang = FakeTurn.hang()
    rig.model.add_turns(hang)
    task = asyncio.create_task(_say(rig, "long job"))
    await asyncio.wait_for(hang.hang_started.wait(), timeout=2.0)

    result, _ = await rig.client.compact()

    assert isinstance(result, ErrorEvent)
    assert result.code == "busy"
    await rig.client.stop()
    await asyncio.wait_for(task, timeout=2.0)


def _seed_read_turn(rig: Rig, index: int, chars: int) -> None:
    """A finished turn whose tool read a long file."""
    memory = rig.host._agent_session.memory
    content = wrap_untrusted("r" * chars, tool="read_file", target=f"doc{index}.md")
    memory.add(TaskStep(task=f"read doc{index}.md"))
    memory.add(
        ActionStep(
            model_message=ChatMessage(role="assistant", content="", tool_calls=[]),
            tool_results=[
                ToolResult(tool_call_id=f"r{index}", name="read_file", content=content)
            ],
        )
    )
    memory.add(FinalStep(answer=f"doc{index}.md read"))


def _chat_tokens(rig: Rig) -> int:
    return rig.host._sizer(None).size(rig.state()) - rig.fixed


async def test_make_room_now_leaves_a_small_chat_after_big_reads(rig: Rig) -> None:
    """Recent long reads are shortened too, so the chat lands near 10% of the window."""
    rig.window(100_000)
    _seed_turns(rig, 20)
    for i in range(4):
        _seed_read_turn(rig, i, 40_000)
    room = rig.host._room()
    assert room is not None and rig.fixed + _chat_tokens(rig) > room.limit
    rig.model.add_turns(FakeTurn.summary())

    result, notices = await rig.client.compact()

    assert isinstance(result, DoneEvent)
    assert notices == [SUMMARISED]
    assert rig.state().shortened_through == len(rig.host._agent_session.memory.steps)
    assert _chat_tokens(rig) <= c.room_max(100_000)


async def test_shortening_under_the_line_still_writes_notes_when_the_chat_is_big(
    rig: Rig,
) -> None:
    """Shortening is only enough when it leaves the chat within 15% of the window."""
    rig.window(100_000)
    _seed_turns(rig, 30)
    for i in range(3):
        _seed_read_turn(rig, i, 40_000)
    rig.model.add_turns(FakeTurn.summary(), _answer())

    events = await _say(rig, "what next?")

    assert isinstance(events[-1], DoneEvent)
    assert _notices(events) == [SUMMARISED]
    assert rig.state().summary is not None
    assert _chat_tokens(rig) <= c.room_max(100_000)


async def test_a_failed_summary_leaves_nothing_out_when_shortening_got_under(
    rig: Rig,
) -> None:
    """No turn leaves the model's view while the request already fits."""
    rig.window(100_000)
    _seed_turns(rig, 30)
    for i in range(3):
        _seed_read_turn(rig, i, 40_000)
    rig.model.add_turns(FakeTurn.text("not notes"), _answer())

    events = await _say(rig, "what next?")

    assert isinstance(events[-1], DoneEvent)
    assert _notices(events) == [SHORTENED]
    state = rig.state()
    assert (state.cut, state.left_out, state.failures) == (0, 0, 1)


async def test_make_room_now_below_the_limit(rig: Rig) -> None:
    """Thirty turns of about 1,000 tokens: more than the tail keeps, under 60%."""
    rig.window(200_000)
    for i in range(30):
        rig.model.add_turns(_answer())
        await _say(rig, _turn_text(i))
    rig.model.add_turns(
        FakeTurn.summary(usage=Usage(input_tokens=7_000, output_tokens=800))
    )
    before = (await rig.client.history()).context

    result, notices = await rig.client.compact()

    assert isinstance(result, DoneEvent)
    assert result.reason == "ok"
    assert result.usage is not None
    assert result.usage.input_tokens == 7_000
    assert before is not None and result.context is not None
    assert result.context.used < before.used
    assert notices == [SUMMARISED]
    state = rig.state()
    assert state.summary is not None
    assert 0 < state.cut < len(rig.host._agent_session.memory.steps)
    assert isinstance(rig.host._agent_session.memory.steps[state.cut], TaskStep)
    assert "compaction" in _stored(rig)


async def test_stop_while_making_room_now(rig: Rig) -> None:
    rig.window(200_000)
    for i in range(30):
        rig.model.add_turns(_answer())
        await _say(rig, _turn_text(i))
    hang = FakeTurn.hang()
    rig.model.add_turns(hang)

    task = asyncio.create_task(rig.client.compact())
    await asyncio.wait_for(hang.hang_started.wait(), timeout=2.0)
    await rig.client.stop()
    result, _ = await asyncio.wait_for(task, timeout=2.0)

    assert isinstance(result, DoneEvent)
    assert result.reason == "stopped"
    assert "compaction" not in _stored(rig)
    rig.model.add_turns(_answer("back"))
    events = await _say(rig, "still there?")
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].reason == "answered"


async def test_make_room_now_says_so_when_the_notes_fail(rig: Rig) -> None:
    rig.window(200_000)
    _seed_turns(rig, 30)
    rig.model.add_turns(FakeTurn.text("not notes"))

    result, notices = await rig.client.compact()

    assert isinstance(result, ErrorEvent)
    assert result.message == c.MESSAGES["notes_failed"]
    assert notices == []
    assert rig.state().failures == 1
    calls = len(rig.model.calls)
    again, _ = await rig.client.compact()
    assert isinstance(again, ErrorEvent)
    assert again.message == c.MESSAGES["notes_failed"]
    assert len(rig.model.calls) == calls


async def test_make_room_now_with_nothing_older_still_shortens(rig: Rig) -> None:
    rig.window(100_000)
    _seed_read_turn(rig, 0, 40_000)

    result, notices = await rig.client.compact()

    assert isinstance(result, DoneEvent)
    assert notices == [SHORTENED]
    assert rig.state().shortened_through == 3
    assert rig.model.calls == []


async def test_make_room_now_on_a_very_small_model_that_still_fits(rig: Rig) -> None:
    """Such models never get notes, so the reply says the chat is short for them."""
    rig.window(int(rig.fixed / 0.65))
    _seed_turns(rig, 2, chars=200)

    result, _ = await rig.client.compact()

    assert isinstance(result, ErrorEvent)
    assert result.message == c.MESSAGES["still_short"]
    assert _summary_calls(rig) == []


async def test_make_room_now_when_another_window_is_answering(rig: Rig) -> None:
    from aldwin.host.conversation_lock import ConversationLock
    from aldwin.host.runner import CONVERSATION_BUSY_MESSAGE

    rig.model.add_turns(_answer())
    await _say(rig, "hello")
    other = ConversationLock(rig.home, rig.host._stored_session.meta.id)
    assert other.try_acquire()
    try:
        result, _ = await rig.client.compact()
    finally:
        other.release()

    assert isinstance(result, ErrorEvent)
    assert result.code == "busy"
    assert result.message == CONVERSATION_BUSY_MESSAGE


# Windows, pieces, read records and other processes


async def test_ollama_uses_the_window_it_sends(aldwin_home: Path) -> None:
    """Room is measured against the num_ctx Aldwin sends, not the advertised size."""
    from aldwin.core.providers.ollama import OllamaModel

    model = OllamaModel("qwen3:8b", context_window=131_072)

    def builder(name: str, ask: AskFn) -> Any:
        return build_agent(
            AldwinConfig(model=name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=model,
        )

    host = AldwinHost(
        InProcessTransport(), agent_builder=builder, initial_model="ollama/qwen3:8b"
    )
    room = host._room()
    assert room is not None
    assert (room.window, room.limit) == (32_768, 19_660)


@pytest.mark.parametrize(("local", "window"), [(False, 128_000), (True, None)])
async def test_hosted_openai_compatible_window_is_known_without_the_model_list(
    aldwin_home: Path, local: bool, window: int | None
) -> None:
    """Hosted models make room before turns in a fresh run; local ones stay unknown."""
    from aldwin.core.providers.openai_chat import OpenAIChatBackend
    from aldwin.core.providers.registry import ProviderEntry

    entry = ProviderEntry(
        name="deepseek",
        title="DeepSeek",
        shape="openai_chat",
        base_url="http://127.0.0.1:8000/v1" if local else "https://api.deepseek.com",
        secret=None if local else "deepseek_api_key",
        local=local,
        docs_url="",
    )
    model = OpenAIChatBackend(entry, api_key="k").build_model("deepseek-v4-pro")

    def builder(name: str, ask: AskFn) -> Any:
        return build_agent(
            AldwinConfig(model=name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=model,
        )

    host = AldwinHost(
        InProcessTransport(),
        agent_builder=builder,
        initial_model="deepseek/deepseek-v4-pro",
    )
    room = host._room()
    assert (room.window if room is not None else None) == window


async def test_local_size_never_trusts_a_cut_count(make_rig: RigFactory) -> None:
    """After a silent cut, Ollama's lower count must not shrink the estimate."""
    rig = await make_rig(model_name="ollama/qwen3:8b")
    measured = {"model": rig.host._model, "room": ""}
    rig.host._stored_session.measured = {
        **measured,
        "tokens": 4_098,
        "estimate": 40_000,
    }
    assert rig.host._real_offset() == 0
    rig.host._stored_session.measured = {
        **measured,
        "tokens": 50_000,
        "estimate": 40_000,
    }
    assert rig.host._real_offset() == 10_000


async def test_requests_stay_byte_identical_between_room_making(rig: Rig) -> None:
    """Within a turn, each request starts with exactly the previous one."""
    rig.window(rig.fixed * 3)
    await _fill_until_summary(rig)
    rig.model.add_turns(
        FakeTurn.tool_call("calculate", {"expression": "1+1"}, call_id="k1"),
        FakeTurn.tool_call("calculate", {"expression": "2+2"}, call_id="k2"),
        FakeTurn.tool_call("calculate", {"expression": "3+3"}, call_id="k3"),
        _answer("done"),
    )
    first = len(rig.model.calls)

    await _say(rig, "add some numbers")

    calls = rig.model.calls[first:]
    assert len(calls) == 4
    for earlier, later in zip(calls, calls[1:], strict=False):
        assert later[: len(earlier)] == earlier


def _seed_turns(rig: Rig, count: int, chars: int = 3_500) -> None:
    memory = rig.host._agent_session.memory
    for i in range(count):
        memory.add(TaskStep(task=_turn_text(i, chars)))
        memory.add(FinalStep(answer=f"a{i}"))


def _planned_groups(rig: Rig, task: str) -> list[int]:
    from aldwin.host.compaction import turn_groups

    room = rig.host._room()
    assert room is not None
    steps = rig.host._agent_session.memory.steps
    task_tokens = c.estimate_tokens([ChatMessage(role="user", content=task)])
    cut = c.choose_cut(
        steps,
        start=0,
        budget=c.tail_budget(room.window),
        new_task_tokens=task_tokens,
        shortened_through=len(steps),
    )
    assert cut is not None
    target = c.summary_target(room.window)
    max_chars = max(2_000, int((room.ceiling - 2 * target - 600) * c.CHARS_PER_TOKEN))
    return turn_groups(steps, 0, cut, max_chars)


async def test_large_history_is_summarised_in_pieces(rig: Rig) -> None:
    rig.window(rig.fixed * 3)
    _seed_turns(rig, 40)
    groups = _planned_groups(rig, "next")
    assert len(groups) >= 2
    rig.model.add_turns(*(FakeTurn.summary() for _ in groups), _answer())

    events = await _say(rig, "next")

    assert _notices(events) == [SUMMARISED]
    calls = _summary_calls(rig)
    assert len(calls) == len(groups)
    assert "<previous_notes>" not in calls[0][1].content
    assert "<previous_notes>" in calls[1][1].content
    assert rig.state().cut == groups[-1]


async def test_a_failed_piece_keeps_the_pieces_before_it(rig: Rig) -> None:
    from agentling import ModelServerError

    rig.window(rig.fixed * 3)
    _seed_turns(rig, 40)
    groups = _planned_groups(rig, "next")
    rig.model.add_turns(
        FakeTurn.summary(), FakeTurn.error(ModelServerError("down")), _answer()
    )

    events = await _say(rig, "next")

    assert isinstance(events[-1], DoneEvent)
    state = rig.state()
    assert state.summary is not None
    assert state.cut >= groups[0]
    assert state.failures == 1
    assert state.retry_after is not None


async def test_retired_reads_must_be_read_again(
    make_rig: RigFactory, tmp_path: Path
) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "notes.md").write_text("draft\n", encoding="utf-8")
    rig = await make_rig(workspace=root)
    policy = rig.host._session_context.policy_session
    rig.window(rig.fixed * 3)
    rig.model.add_turns(
        FakeTurn.tool_call("read_file", {"path": "notes.md"}, call_id="r1"),
        _answer("read it"),
    )
    await _say(rig, "read my notes")
    resolved = (root / "notes.md").resolve()
    assert resolved in policy.read_files

    await _fill_until_summary(rig, 1)

    assert rig.state().cut > 0
    assert resolved not in policy.read_files
    assert any(entry.path == "notes.md" for entry in rig.state().files)


async def test_another_process_sees_the_summary(make_rig: RigFactory) -> None:
    first = await make_rig()
    first.window(first.fixed * 3)
    second = await make_rig()
    second.window(second.fixed * 3)
    await _fill_until_summary(first)
    second.model.add_turns(_answer())

    await _say(second, "from the other window")

    sent = second.model.calls[-1]
    first_user = next(m for m in sent if m.role == "user")
    assert first_user.content.startswith(c.SUMMARY_PREAMBLE)


async def test_new_and_reopened_chats_use_their_own_notes(rig: Rig) -> None:
    rig.window(rig.fixed * 3)
    await _fill_until_summary(rig)
    compacted = rig.host._stored_session.meta.id

    await rig.client.new_conversation()
    assert rig.host._stored_session.measured is None
    rig.model.add_turns(_answer())
    await _say(rig, "fresh start")
    users = [m for m in rig.model.calls[-1] if m.role == "user"]
    assert users[0].content == "fresh start"

    await rig.client.sessions_open(compacted)
    rig.model.add_turns(_answer())
    await _say(rig, "back again")
    first_user = next(m for m in rig.model.calls[-1] if m.role == "user")
    assert first_user.content.startswith(c.SUMMARY_PREAMBLE)
