"""Named module for `recall` (see `test_memory_tools` / `test_memory_index`)."""

from __future__ import annotations

from pathlib import Path

from agentling import ChatMessage, ToolCall
from agentling.memory import (
    ActionStep,
    FinalStep,
    Memory,
    Step,
    TaskStep,
    ToolResult,
)

from aldwin.core.memory_index import search_session_file
from aldwin.core.policy import REGISTRY
from aldwin.core.tools.memory_tools import RecallTool, running_summary
from aldwin.host.sessions import SessionStore


def test_recall_registry_and_running_summary() -> None:
    """`recall` is Auto and has a stable running-summary verb."""
    assert REGISTRY["recall"].tier == "auto"
    assert REGISTRY["recall"].writes is False
    tool = RecallTool(home=Path("/tmp"))
    assert tool.name == "recall"
    assert "Recall" in running_summary("recall", {"query": "address"})


def test_recall_scope_parameter_in_schema() -> None:
    """Schema advertises `scope` for notes / conversations / both."""
    props = RecallTool(home=Path("/tmp")).parameters["properties"]
    assert "scope" in props
    assert "query" in props
    assert "this_conversation" in props["scope"]["enum"]
    assert "this_conversation" in RecallTool.description


def _saved_chat(tmp_path: Path, steps: list[Step]) -> Path:
    store = SessionStore(tmp_path / "sessions")
    stored = store.create_new(model="gpt-4o")
    memory = Memory()
    for step in steps:
        memory.add(step)
    store.save(stored, memory, interrupted=False)
    return stored.path


def _tool_turn(turn: int, content: str) -> list[Step]:
    call = ToolCall(id=f"c{turn}", name="read_file", arguments={"path": f"f{turn}.md"})
    return [
        TaskStep(task=f"question {turn}"),
        ActionStep(
            model_message=ChatMessage(role="assistant", content="", tool_calls=[call]),
            tool_results=[
                ToolResult(tool_call_id=f"c{turn}", name="read_file", content=content)
            ],
        ),
        FinalStep(answer=f"answer {turn}"),
    ]


def test_this_conversation_searches_tool_results_newest_first(tmp_path: Path) -> None:
    steps: list[Step] = []
    for turn in range(1, 8):
        steps += _tool_turn(turn, f"invoice number {turn} " + "x" * 3_000)
    path = _saved_chat(tmp_path, steps)

    result = search_session_file(path, "Invoice")

    blocks = result.split("\n\n")[1:]
    assert len(blocks) == 5
    assert blocks[0].startswith("[turn 7 · tool result from read_file]")
    assert blocks[-1].startswith("[turn 3 · tool result from read_file]")
    assert all(len(block) < 1_600 for block in blocks)


def test_this_conversation_prefers_passages_with_every_word(tmp_path: Path) -> None:
    steps = _tool_turn(1, "free cancellation until 10 May") + _tool_turn(
        2, "free parking"
    )
    path = _saved_chat(tmp_path, steps)

    result = search_session_file(path, "free cancellation")

    assert "[turn 1 · tool result from read_file]" in result
    assert "free parking" not in result


def test_this_conversation_no_match(tmp_path: Path) -> None:
    path = _saved_chat(tmp_path, _tool_turn(1, "hello"))
    assert search_session_file(path, "zzz") == "(no matches in this conversation)"
    assert search_session_file(path, "  ") == "(no matches in this conversation)"
    assert search_session_file(tmp_path / "missing.json", "hello") == (
        "(no matches in this conversation)"
    )


async def test_recall_this_conversation_without_a_saved_chat() -> None:
    tool = RecallTool(current_session=lambda: None)
    result = await tool.forward("anything", scope="this_conversation")
    assert result.ok is False
    assert str(result) == "this conversation has not been saved yet"


async def test_recall_this_conversation_reads_the_active_chat(tmp_path: Path) -> None:
    path = _saved_chat(tmp_path, _tool_turn(1, "the hotel code is 4417"))
    tool = RecallTool(current_session=lambda: path)

    result = await tool.forward("hotel code", scope="this_conversation")

    assert result.ok is True
    assert "4417" in str(result)
    assert result.summary == 'Recall "hotel code" · 1 in this chat'
