"""Tests for `aldwin.core.memory_index`."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from perf_slack import budget

from aldwin.core.home import FILE_MODE, atomic_write
from aldwin.core.memory_index import (
    FTS5_UNAVAILABLE_MSG,
    MemoryIndex,
    build_fts_query,
    synthetic_note_id,
)


def _index(tmp_path: Path) -> MemoryIndex:
    notes = tmp_path / "notes"
    sessions = tmp_path / "sessions"
    notes.mkdir()
    sessions.mkdir()
    return MemoryIndex(notes, sessions, tmp_path / "index.sqlite")


def _write_note(notes_dir: Path, day: str, lines: list[str]) -> Path:
    path = notes_dir / f"{day}.md"
    body = f"# Notes {day}\n<!-- aldwin-memory v1 -->\n" + "\n".join(lines) + "\n"
    atomic_write(path, body, mode=FILE_MODE)
    return path


def _write_session(
    sessions_dir: Path,
    session_id: str,
    *,
    updated_at: str,
    steps: list[dict[str, object]],
) -> Path:
    path = sessions_dir / f"{session_id}.json"
    payload = {
        "meta": {
            "id": session_id,
            "created_at": updated_at,
            "updated_at": updated_at,
            "model": "gpt-4o",
            "title": "t",
            "interrupted": False,
        },
        "memory": json.dumps({"version": 1, "steps": steps}),
    }
    atomic_write(path, json.dumps(payload, indent=2) + "\n", mode=FILE_MODE)
    return path


def test_fts5_available_true_on_this_python() -> None:
    assert MemoryIndex.fts5_available() is True


def test_open_creates_index_0600_and_user_version_1(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    idx.sync()
    assert idx.index_path.is_file()
    assert (idx.index_path.stat().st_mode & 0o777) == 0o600
    conn = sqlite3.connect(str(idx.index_path))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        names = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        assert "notes" in names
        assert "turns" in names
        assert "files" in names
    finally:
        conn.close()


def test_corrupt_index_rebuilds(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    idx = _index(tmp_path)
    _write_note(
        idx.notes_dir,
        "2026-10-01",
        ["- running shoes size 10 <!-- id=m_aaaaaaaaaaaa at=t src=s -->"],
    )
    idx.sync()
    idx.index_path.write_bytes(b"not a sqlite database!!!")
    with caplog.at_level(logging.INFO, logger="aldwin.core.memory_index"):
        result = idx.search("running")
    assert "running shoes" in result
    assert any("rebuilt" in r.message.lower() for r in caplog.records)


def test_stemming_recall_returns_both_run_notes(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    _write_note(
        idx.notes_dir,
        "2026-10-20",
        [
            "- running shoes size 10 <!-- id=m_bbbbbbbbbbbb at=t src=s -->",
            "- runs 5k today <!-- id=m_cccccccccccc at=t src=s -->",
        ],
    )
    idx.sync()
    result = idx.search("run")
    assert "running shoes" in result
    assert "runs 5k" in result
    assert "(id m_bbbbbbbbbbbb)" in result


def test_day_window_widens_with_prefix(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    old_day = (date.today() - timedelta(days=40)).isoformat()
    _write_note(
        idx.notes_dir,
        old_day,
        ["- car insurance renews in March <!-- id=m_dddddddddddd at=t src=s -->"],
    )
    idx.sync()
    result = idx.search("insurance", days=30)
    assert result.startswith("(no matches in the last 30 days; showing older)")
    assert "insurance renews" in result


def test_both_scope_does_not_widen_when_conversations_hit(tmp_path: Path) -> None:
    """In-window conversation hits must not pull in older notes or the widen banner."""
    idx = _index(tmp_path)
    today = date.today()
    old_day = (today - timedelta(days=40)).isoformat()
    recent_day = today.isoformat()
    _write_note(
        idx.notes_dir,
        old_day,
        ["- flat deposit paid last year <!-- id=m_oldflat00001 at=t src=s -->"],
    )
    _write_session(
        idx.sessions_dir,
        f"{recent_day}_abc",
        updated_at=f"{recent_day}T12:00:00+00:00",
        steps=[
            {"type": "task", "data": {"task": "what about the flat"}},
            {
                "type": "action",
                "data": {
                    "model_message": {
                        "role": "assistant",
                        "content": "we chose the flat in Chelmsford",
                    },
                    "tool_results": [],
                },
            },
            {"type": "final", "data": {"answer": "Chelmsford flat"}},
        ],
    )
    idx.sync()
    result = idx.search("flat", scope="both", days=30)
    assert "(no matches in the last" not in result
    assert "chose the flat" in result
    assert "deposit paid" not in result


def test_conversation_scope_indexes_assistant_not_tools(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    _write_session(
        idx.sessions_dir,
        "2026-10-01_abc",
        updated_at="2026-10-01T12:00:00+00:00",
        steps=[
            {"type": "task", "data": {"task": "what about the flat"}},
            {
                "type": "action",
                "data": {
                    "model_message": {
                        "role": "assistant",
                        "content": "we chose Chelmsford",
                    },
                    "tool_results": [
                        {
                            "tool_call_id": "c1",
                            "name": "recall",
                            "content": "UNIQUE_TOOL_TOKEN_XYZ",
                        }
                    ],
                },
            },
            {"type": "final", "data": {"answer": "Chelmsford it is"}},
        ],
    )
    idx.sync()
    result = idx.search("Chelmsford", scope="conversations")
    assert "Chelmsford" in result
    assert "assistant:" in result
    assert "session 2026-10-01_abc" in result
    assert "UNIQUE_TOOL_TOKEN_XYZ" not in result
    tool_search = idx.search("UNIQUE_TOOL_TOKEN_XYZ", scope="conversations")
    assert tool_search.startswith("nothing matches")


def test_fts_operators_are_literal(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    _write_note(
        idx.notes_dir,
        "2026-10-02",
        ["- hello world <!-- id=m_eeeeeeeeeeee at=t src=s -->"],
    )
    idx.sync()
    # Must not raise; operators stay quoted.
    result = idx.search('foo" OR text:*')
    assert "sqlite" not in result.lower() or "nothing matches" in result


def test_hand_edit_reindexes(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    path = _write_note(
        idx.notes_dir,
        "2026-10-03",
        ["- old address Elm <!-- id=m_ffffffffffff at=t src=s -->"],
    )
    idx.sync()
    assert "Elm" in idx.search("address")
    time.sleep(0.01)
    atomic_write(
        path,
        "# Notes 2026-10-03\n<!-- aldwin-memory v1 -->\n"
        "- new address Mill <!-- id=m_ffffffffffff at=t src=s -->\n",
        mode=FILE_MODE,
    )
    result = idx.search("address")
    assert "Mill" in result
    assert "Elm" not in result


def test_synthetic_id_stable(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    path = _write_note(idx.notes_dir, "2026-10-04", ["- bare note without id"])
    idx.sync()
    expected = synthetic_note_id(path, "bare note without id")
    assert expected.startswith("n-")
    assert len(expected) == 6
    result = idx.search("bare")
    assert f"(id {expected})" in result
    idx.sync()
    assert f"(id {expected})" in idx.search("bare")


def test_build_fts_query_quotes_words() -> None:
    assert build_fts_query('foo" OR bar') == '"foo""" OR "OR" OR "bar"'


def test_fallback_without_fts5(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(MemoryIndex, "fts5_available", staticmethod(lambda: False))
    # Reset module fallback log flag.
    import aldwin.core.memory_index as mi

    monkeypatch.setattr(mi, "_FTS5_FALLBACK_LOGGED", False)
    idx = _index(tmp_path)
    _write_note(
        idx.notes_dir,
        "2026-10-05",
        ["- running shoes <!-- id=m_111111111111 at=t src=s -->"],
    )
    with caplog.at_level(logging.INFO, logger="aldwin.core.memory_index"):
        notes = idx.search("running", scope="notes")
        conv = idx.search("anything", scope="conversations")
    assert "running shoes" in notes
    assert conv == FTS5_UNAVAILABLE_MSG
    assert any("FTS5 unavailable" in r.message for r in caplog.records)


def test_empty_and_no_match(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    assert idx.search("") == "query is empty"
    assert idx.search("zzzz") == 'nothing matches "zzzz"'


def test_stats_and_size(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    _write_note(
        idx.notes_dir,
        "2026-10-06",
        ["- one <!-- id=m_222222222222 at=t src=s -->"],
    )
    idx.sync()
    notes, turns, size = idx.stats()
    assert notes == 1
    assert turns == 0
    assert size > 0


@pytest.mark.perf
def test_perf_budget_rebuild_and_query(tmp_path: Path) -> None:
    idx = _index(tmp_path)
    base = date(2020, 1, 1)
    for i in range(3650):
        day = (base + timedelta(days=i % 365)).isoformat()
        path = idx.notes_dir / f"{day}.md"
        line = f"- note {i} keyword apple <!-- id=m_{i:012d} at=t src=s -->\n"
        with path.open("a", encoding="utf-8") as fh:
            if path.stat().st_size == 0 if path.exists() else True:
                pass
            fh.write(line)
    for i in range(200):
        _write_session(
            idx.sessions_dir,
            f"sess_{i}",
            updated_at=f"2026-01-{(i % 28) + 1:02d}T12:00:00+00:00",
            steps=[
                {"type": "task", "data": {"task": f"question {i} apple"}},
                {"type": "final", "data": {"answer": f"answer {i}"}},
            ],
        )
    # Ensure note files exist with headers for days we appended to blindly.
    for path in idx.notes_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("#"):
            path.write_text(
                f"# Notes {path.stem}\n<!-- aldwin-memory v1 -->\n{text}",
                encoding="utf-8",
            )

    t0 = time.perf_counter()
    idx.sync()
    rebuild_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    idx.sync()
    noop_s = time.perf_counter() - t1
    t2 = time.perf_counter()
    result = idx.search("apple")
    query_s = time.perf_counter() - t2
    assert "apple" in result
    assert rebuild_s <= budget(2.0), f"rebuild {rebuild_s:.3f}s"
    assert noop_s <= budget(0.030), f"noop sync {noop_s:.3f}s"
    assert query_s <= budget(0.050), f"query {query_s:.3f}s"
