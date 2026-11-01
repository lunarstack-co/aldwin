"""Tests for `aldwin.host.sessions` persistence, resume, and history rendering."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from agentling import Agent, tool
from agentling.memory import ActionStep, FinalStep, Memory, TaskStep, ToolResult
from agentling.models import ChatMessage, ToolCall
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
    assert fallback.meta.model == "gpt-4o-mini"


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
