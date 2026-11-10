"""Tests for `aldwin.host.sessions` persistence, resume, and history rendering."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from agentling import Agent, ChatMessage, ToolCall, tool
from agentling.memory import ActionStep, FinalStep, Memory, TaskStep, ToolResult
from fakes.model import FakeModel, FakeTurn

from aldwin.host.sessions import SessionStore
from aldwin.protocol import HistoryItem


def test_session_save_creates_file_with_0600_and_derives_60_char_title(
    aldwin_home: Path,
) -> None:
    """TC-24: First user message sets `meta.title` (<= 60 chars) and file mode 0600."""
    store = SessionStore()
    stored = store.create_new(model="gpt-4o")
    assert stored.path.exists()
    assert (stored.path.stat().st_mode & 0o777) == 0o600
    assert stored.meta.title == ""

    long_prompt = (
        "   Explain   how\nasyncio   task cancellation works in Python 3.11 "
        "with extra trailing words that go past sixty characters   "
    )
    stored.memory.add(TaskStep(task=long_prompt))
    store.save(stored, stored.memory, interrupted=True)

    expected_title = "Explain how asyncio task cancellation works in Python 3.11 w"
    assert len(expected_title) == 60
    assert stored.meta.title == expected_title
    first_created = stored.meta.created_at
    first_updated = stored.meta.updated_at

    stored.memory.add(FinalStep(answer="Done."))
    stored.memory.add(TaskStep(task="Second question"))
    stored.memory.add(FinalStep(answer="Second answer"))
    store.save(stored, stored.memory, interrupted=False)

    reloaded = store.load_latest()
    assert reloaded.meta.title == expected_title
    assert reloaded.meta.created_at == first_created
    assert reloaded.meta.updated_at >= first_updated
    assert reloaded.meta.interrupted is False


def test_crash_after_step_2_reloads_steps_and_sets_interrupted_true(
    aldwin_home: Path,
) -> None:
    """TC-11: Crash after step 2 reloads steps 1-2 intact with `interrupted=True`."""
    store = SessionStore()
    stored = store.create_new(model="gpt-4o")

    stored.memory.add(TaskStep(task="Check disk and uptime"))
    step1 = ActionStep(
        model_message=ChatMessage(
            role="assistant",
            content="Checking disk.",
            tool_calls=[ToolCall(id="c1", name="run_command", arguments={"cmd": "df"})],
        ),
        tool_results=[
            ToolResult(tool_call_id="c1", name="run_command", content="41 GB free")
        ],
    )
    stored.memory.add(step1)
    store.save(stored, stored.memory, interrupted=True)

    step2 = ActionStep(
        model_message=ChatMessage(
            role="assistant",
            content="Checking uptime.",
            tool_calls=[
                ToolCall(id="c2", name="run_command", arguments={"cmd": "uptime"})
            ],
        ),
        tool_results=[
            ToolResult(tool_call_id="c2", name="run_command", content="up 5 days")
        ],
    )
    stored.memory.add(step2)
    store.save(stored, stored.memory, interrupted=True)

    restarted_store = SessionStore()
    resumed = restarted_store.load_latest()

    assert resumed.meta.id == stored.meta.id
    assert resumed.meta.interrupted is True
    assert len(resumed.memory.steps) == 3
    assert isinstance(resumed.memory.steps[0], TaskStep)
    assert isinstance(resumed.memory.steps[1], ActionStep)
    assert isinstance(resumed.memory.steps[2], ActionStep)


def test_create_new_starts_clean_session_and_leaves_previous_file_untouched(
    aldwin_home: Path,
) -> None:
    """TC-12: `create_new` creates a new session file without modifying the old one."""
    store = SessionStore()
    first = store.create_new(model="gpt-4o")
    first.memory.add(TaskStep(task="First conversation"))
    first.memory.add(FinalStep(answer="First reply"))
    store.save(first, first.memory, interrupted=False)
    first_bytes = first.path.read_bytes()

    second = store.create_new(model="gpt-4.1")
    assert second.path != first.path
    assert first.path.read_bytes() == first_bytes
    assert second.memory.steps == []
    assert second.meta.interrupted is False

    latest = store.load_latest()
    assert latest.meta.id == second.meta.id


def test_atomic_save_survives_interrupted_write_and_skips_corrupt_file(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-23: Mid-write failure leaves original file intact; corrupt file is skipped."""
    store = SessionStore()
    stored = store.create_new(model="gpt-4o")
    stored.memory.add(TaskStep(task="Safe turn"))
    stored.memory.add(FinalStep(answer="Safe answer"))
    store.save(stored, stored.memory, interrupted=False)
    original_content = stored.path.read_text(encoding="utf-8")

    orig_replace = os.replace

    def boom_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("simulated disk error on replace")

    monkeypatch.setattr(os, "replace", boom_replace)
    stored.memory.add(TaskStep(task="Unsaved turn"))
    with pytest.raises(OSError, match="simulated disk error"):
        store.save(stored, stored.memory, interrupted=True)

    monkeypatch.setattr(os, "replace", orig_replace)
    assert stored.path.read_text(encoding="utf-8") == original_content
    tmp_files = list(store.sessions_dir.glob("*.tmp"))
    assert tmp_files == []

    corrupt_path = store.sessions_dir / "2099-12-31_corrupt1.json"
    corrupt_path.write_text("{corrupt json", encoding="utf-8")

    resumed = store.load_latest()
    assert resumed.meta.id == stored.meta.id
    assert len(resumed.memory.steps) == 2

    corrupt_path.unlink()
    stored.path.write_text(json.dumps({"bad": "shape"}), encoding="utf-8")
    fallback = store.load_latest(default_model="gpt-4o-mini")
    assert fallback.meta.id != stored.meta.id
    assert fallback.meta.model == "openai/gpt-4o-mini"


def test_render_history_windows_last_limit_and_summarises_tools(
    aldwin_home: Path,
) -> None:
    """TC-26: `render_history` returns last `limit` messages + 1-line tool summaries."""
    store = SessionStore()
    memory = Memory()

    for i in range(15):
        memory.add(TaskStep(task=f"user {i}"))
        if i == 14:
            memory.add(
                ActionStep(
                    model_message=ChatMessage(
                        role="assistant",
                        content="Let me run df.",
                        tool_calls=[
                            ToolCall(
                                id="tc_1",
                                name="run_command",
                                arguments={"command": "df -h"},
                            )
                        ],
                    ),
                    tool_results=[
                        ToolResult(
                            tool_call_id="tc_1",
                            name="run_command",
                            content="41 GB free\nsecond line ignored",
                            is_error=False,
                        )
                    ],
                )
            )
        memory.add(FinalStep(answer=f"assistant {i}"))

    history = store.render_history(memory, limit=20)
    ua_items = [item for item in history if item.role in ("user", "assistant")]
    tool_items = [item for item in history if item.role == "tool"]

    assert len(ua_items) == 20
    assert ua_items[0] == HistoryItem(role="user", text="user 5")
    assert ua_items[-1] == HistoryItem(role="assistant", text="assistant 14")
    assert tool_items == [HistoryItem(role="tool", text="✓ run_command · 41 GB free")]
    assert store.render_history(memory, limit=0) == []


async def test_fake_model_drives_two_step_agent_session_and_saves(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Step 4 check: `FakeModel` drives a 2-step `AgentSession` without network."""

    @tool
    def echo(msg: str) -> str:
        """Echo back `msg`.

        Args:
            msg: Text to echo.
        """
        return f"echoed: {msg}"

    fake_model.add_turns(
        FakeTurn.tool_call("echo", {"msg": "hello"}),
        FakeTurn.text(["Done ", "now."]),
    )
    agent = Agent(model=fake_model, tools=[echo])
    session = agent.start()

    store = SessionStore()
    stored = store.create_new(model="gpt-4o")

    async for _ev in session.run("Run echo", stream=True, reset=False):
        pass

    store.save(stored, session.memory, interrupted=False)
    reloaded = store.load_latest()
    assert reloaded.meta.title == "Run echo"
    assert reloaded.meta.interrupted is False
    assert len(reloaded.memory.steps) == 3
    assert len(fake_model.calls) == 2


async def test_resume_003_memory_session_fixture_upgrades_to_version_2(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """A 0.0.3 session JSON (memory v1) resumes a turn and saves as memory v2."""
    sessions_dir = aldwin_home / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    store = SessionStore(sessions_dir)

    v1_memory_json = json.dumps(
        {
            "version": 1,
            "steps": [
                {"type": "task", "data": {"task": "Explain 0.0.3"}},
                {"type": "final", "data": {"answer": "It had connectors."}},
            ],
        }
    )
    session_file = sessions_dir / "2026-10-30_003test.json"
    session_payload = {
        "meta": {
            "id": "2026-10-30_003test",
            "created_at": "2026-10-30T10:00:00+00:00",
            "updated_at": "2026-10-30T10:00:00+00:00",
            "model": "gpt-4o",
            "title": "Explain 0.0.3",
            "interrupted": False,
        },
        "memory": v1_memory_json,
    }
    session_file.write_text(json.dumps(session_payload), encoding="utf-8")

    loaded = store.load_latest()
    assert loaded is not None
    assert loaded.meta.id == "2026-10-30_003test"
    assert len(loaded.memory.steps) == 2

    fake_model.add_turns(FakeTurn.text(["0.0.4 with Agentling 0.2.0."]))
    agent = Agent(model=fake_model)
    session = agent.start()
    session.memory = loaded.memory
    async for _ev in session.run("What comes next?", stream=True, reset=False):
        pass

    store.save(loaded, session.memory, interrupted=False)

    saved_raw = json.loads(session_file.read_text(encoding="utf-8"))
    saved_memory = json.loads(saved_raw["memory"])
    assert saved_memory["version"] == 2
    assert len(saved_memory["steps"]) == 4


def test_legacy_bare_model_id_normalized(aldwin_home: Path) -> None:
    """Loading a 0.0.3 session with a bare model ID normalizes it to openai/<id>."""
    sessions_dir = aldwin_home / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    store = SessionStore(sessions_dir)

    session_file = sessions_dir / "2026-10-25_legacy.json"
    session_payload = {
        "meta": {
            "id": "2026-10-25_legacy",
            "created_at": "2026-10-25T10:00:00+00:00",
            "updated_at": "2026-10-25T10:00:00+00:00",
            "model": "gpt-4o",
            "title": "Legacy session",
            "interrupted": False,
        },
        "memory": json.dumps({"version": 2, "steps": []}),
    }
    session_file.write_text(json.dumps(session_payload), encoding="utf-8")

    loaded = store.load_file(session_file)
    assert loaded is not None
    assert loaded.meta.model == "openai/gpt-4o"


def test_save_session_full_provider_ref(aldwin_home: Path) -> None:
    """Saving a session preserves the full provider/model reference."""
    store = SessionStore()
    stored = store.create_new(model="anthropic/claude-3-7-sonnet")
    assert stored.meta.model == "anthropic/claude-3-7-sonnet"

    store.save(stored, stored.memory, interrupted=False)
    raw = json.loads(stored.path.read_text(encoding="utf-8"))
    assert raw["meta"]["model"] == "anthropic/claude-3-7-sonnet"


def _write_session(
    sessions_dir: Path,
    session_id: str,
    *,
    updated_at: str = "2026-11-01T10:00:00+00:00",
    title: str = "",
    turns: list[dict[str, object]] | None = None,
    name: str | None = None,
) -> Path:
    """Write a session file by hand, the way older and newer versions saved them."""
    sessions_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "meta": {
            "id": session_id,
            "created_at": "2026-11-01T09:00:00+00:00",
            "updated_at": updated_at,
            "model": "gpt-4o",
            "title": title,
            "interrupted": False,
        },
        "memory": Memory().dump_json(),
    }
    if turns is not None:
        payload["turns"] = turns
    path = sessions_dir / (name or f"{session_id}.json")
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _turn(text: str) -> dict[str, object]:
    return {"text": text, "skills": []}


def test_list_metas_hides_zero_turn_sessions(aldwin_home: Path) -> None:
    """TC-01: empty sessions are left out of the history list."""
    sessions_dir = aldwin_home / "sessions"
    _write_session(sessions_dir, "2026-11-01_full", title="Hi", turns=[_turn("Hi")])
    _write_session(sessions_dir, "2026-11-01_empty")

    store = SessionStore(sessions_dir)
    assert [m.id for m in store.list_metas()] == ["2026-11-01_full"]
    everything = store.list_metas(include_empty=True)
    assert {m.id for m in everything} == {"2026-11-01_full", "2026-11-01_empty"}
    assert {m.id: m.has_turns for m in everything} == {
        "2026-11-01_full": True,
        "2026-11-01_empty": False,
    }


def test_list_metas_skips_corrupt_files(
    aldwin_home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """TC-02: corrupt JSON and wrong shapes are skipped; the rest are listed."""
    sessions_dir = aldwin_home / "sessions"
    _write_session(sessions_dir, "2026-11-01_good", title="Ok", turns=[_turn("Ok")])
    (sessions_dir / "2026-11-01_broken.json").write_text("{not json", "utf-8")
    (sessions_dir / "2026-11-01_list.json").write_text("[1, 2]", "utf-8")
    (sessions_dir / "2026-11-01_nometa.json").write_text(
        json.dumps({"memory": "{}"}), "utf-8"
    )
    (sessions_dir / "2026-11-01_noid.json").write_text(
        json.dumps({"meta": {"title": "x"}, "memory": "{}"}), "utf-8"
    )

    metas = SessionStore(sessions_dir).list_metas(include_empty=True)

    assert [m.id for m in metas] == ["2026-11-01_good"]
    assert caplog.text.count("Skipping unreadable session file") == 4


def test_list_metas_orders_newest_first(aldwin_home: Path) -> None:
    """TC-03: newest `updated_at` first, then the newer file on a tie."""
    sessions_dir = aldwin_home / "sessions"
    turns = [_turn("x")]
    old = _write_session(
        sessions_dir,
        "a_old",
        updated_at="2026-11-01T08:00:00+00:00",
        title="old",
        turns=turns,
    )
    _write_session(
        sessions_dir,
        "b_new",
        updated_at="2026-11-03T08:00:00+00:00",
        title="new",
        turns=turns,
    )
    tie_first = _write_session(
        sessions_dir,
        "c_tie1",
        updated_at="2026-11-02T08:00:00+00:00",
        title="t1",
        turns=turns,
    )
    tie_second = _write_session(
        sessions_dir,
        "d_tie2",
        updated_at="2026-11-02T08:00:00+00:00",
        title="t2",
        turns=turns,
    )
    os.utime(old, ns=(1, 1))
    os.utime(tie_first, ns=(2_000_000_000, 2_000_000_000))
    os.utime(tie_second, ns=(1_000_000_000, 1_000_000_000))

    ids = [m.id for m in SessionStore(sessions_dir).list_metas()]

    assert ids == ["b_new", "c_tie1", "d_tie2", "a_old"]


def test_list_metas_ignores_non_json_and_dotfiles(aldwin_home: Path) -> None:
    """TC-04: only visible `*.json` files are sessions."""
    sessions_dir = aldwin_home / "sessions"
    _write_session(sessions_dir, "real", title="r", turns=[_turn("r")])
    _write_session(
        sessions_dir, "hidden", title="h", turns=[_turn("h")], name=".hidden.json"
    )
    _write_session(sessions_dir, "text", title="t", turns=[_turn("t")], name="text.txt")
    (sessions_dir / "folder.json").mkdir()

    assert [m.id for m in SessionStore(sessions_dir).list_metas()] == ["real"]


def test_list_metas_lists_legacy_titled_without_turns(aldwin_home: Path) -> None:
    """TC-05: an older file with a title but no `turns` key still has turns."""
    sessions_dir = aldwin_home / "sessions"
    legacy = Memory()
    legacy.add(TaskStep(task="Old question"))
    legacy.add(FinalStep(answer="Old answer"))
    path = _write_session(sessions_dir, "legacy", title="Old question")
    raw = json.loads(path.read_text("utf-8"))
    raw["memory"] = legacy.dump_json()
    path.write_text(json.dumps(raw), "utf-8")

    metas = SessionStore(sessions_dir).list_metas()

    assert [(m.id, m.title, m.has_turns) for m in metas] == [
        ("legacy", "Old question", True)
    ]


def test_list_metas_hides_legacy_empty_title_without_turns(aldwin_home: Path) -> None:
    """TC-06: no `turns` key and no title counts as an empty session."""
    sessions_dir = aldwin_home / "sessions"
    _write_session(sessions_dir, "legacy_empty")

    assert SessionStore(sessions_dir).list_metas() == []


def test_list_metas_empty_turns_list_is_zero_turn(aldwin_home: Path) -> None:
    """TC-07: `turns: []` with an empty title is an empty session."""
    sessions_dir = aldwin_home / "sessions"
    _write_session(sessions_dir, "empty_turns", turns=[])

    store = SessionStore(sessions_dir)
    assert store.list_metas() == []
    assert store.list_metas(include_empty=True)[0].has_turns is False


def test_list_metas_does_not_call_memory_load_json(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-08: listing reads metadata only, never the conversation."""
    sessions_dir = aldwin_home / "sessions"
    _write_session(sessions_dir, "one", title="One", turns=[_turn("One")])

    def fail(_raw: str) -> Memory:
        raise AssertionError("list_metas must not parse the conversation")

    monkeypatch.setattr(Memory, "load_json", staticmethod(fail))

    assert [m.id for m in SessionStore(sessions_dir).list_metas()] == ["one"]


def test_load_id_returns_stored_session(aldwin_home: Path) -> None:
    """TC-09: a saved session loads by its id."""
    store = SessionStore()
    stored = store.create_new(model="gpt-4o")
    stored.memory.add(TaskStep(task="Find me"))
    stored.turns = [{"text": "Find me", "skills": []}]
    store.save(stored, stored.memory, interrupted=False)

    loaded = store.load_id(stored.meta.id)

    assert loaded is not None
    assert loaded.path == stored.path
    assert loaded.meta.title == "Find me"
    assert loaded.turns == [{"text": "Find me", "skills": []}]


@pytest.mark.parametrize(
    "session_id",
    ["2026-11-01_missing", "../sessions/x", "a/b", ".hidden", "", "x.json"],
)
def test_load_id_missing_returns_none(aldwin_home: Path, session_id: str) -> None:
    """TC-10: unknown ids and ids that are not plain file names return `None`."""
    store = SessionStore()
    store.create_new(model="gpt-4o")

    assert store.load_id(session_id) is None


def test_load_id_corrupt_returns_none(aldwin_home: Path) -> None:
    """TC-11: a corrupt file, or one whose stored id differs, returns `None`."""
    sessions_dir = aldwin_home / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "broken.json").write_text("{not json", "utf-8")
    _write_session(sessions_dir, "other_id", name="renamed.json")

    store = SessionStore(sessions_dir)
    assert store.load_id("broken") is None
    assert store.load_id("renamed") is None


def test_zero_turn_meta_title_is_empty(aldwin_home: Path) -> None:
    """TC-12: a new session, saved or reloaded, keeps an empty title."""
    store = SessionStore()
    stored = store.create_new(model="gpt-4o")
    store.save(stored, stored.memory, interrupted=False)

    assert stored.meta.title == ""
    reloaded = store.load_id(stored.meta.id)
    assert reloaded is not None and reloaded.meta.title == ""
    assert store.list_metas(include_empty=True)[0].title == ""


def test_session_disk_schema_keys_unchanged(aldwin_home: Path) -> None:
    """TC-13: the on-disk layout is still `meta`, `memory`, `todo`, `turns`."""
    store = SessionStore()
    stored = store.create_new(model="gpt-4o")
    raw = json.loads(stored.path.read_text("utf-8"))
    assert set(raw) == {"meta", "memory"}
    assert set(raw["meta"]) == {
        "id",
        "created_at",
        "updated_at",
        "model",
        "title",
        "interrupted",
    }

    stored.memory.add(TaskStep(task="Plan"))
    stored.turns = [{"text": "Plan", "skills": []}]
    store.save(
        stored,
        stored.memory,
        interrupted=False,
        todo=[{"text": "step", "status": "pending"}],
    )
    raw = json.loads(stored.path.read_text("utf-8"))
    assert set(raw) == {"meta", "memory", "todo", "turns"}


def test_save_without_touch_keeps_updated_at(aldwin_home: Path) -> None:
    """Saving a session the user is leaving does not reorder history."""
    store = SessionStore()
    stored = store.create_new(model="gpt-4o")
    before = stored.meta.updated_at

    store.save(stored, stored.memory, interrupted=False, touch=False)

    raw = json.loads(stored.path.read_text("utf-8"))
    assert raw["meta"]["updated_at"] == before


def test_list_metas_without_sessions_folder_is_empty(tmp_path: Path) -> None:
    """Before the first conversation there is nothing to list or load."""
    store = SessionStore(tmp_path / "missing")

    assert store.list_metas(include_empty=True) == []
    assert store.load_id("2026-11-01_abcd1234") is None


def test_list_metas_treats_malformed_turns_as_empty(aldwin_home: Path) -> None:
    """A `turns` value that is not a list of messages counts as no turns."""
    sessions_dir = aldwin_home / "sessions"
    _write_session(sessions_dir, "odd", turns=["garbage"])  # type: ignore[list-item]

    store = SessionStore(sessions_dir)
    assert store.list_metas() == []
    loaded = store.load_id("odd")
    assert loaded is not None and loaded.turns is None
