"""Rebuildable SQLite FTS5 index over memory notes and saved sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final, Literal

from aldwin.core.home import FILE_MODE
from aldwin.core.memory import (
    DATE_STEM_RE,
    DEFAULT_RECALL_DAYS,
    parse_note_line,
)

logger = logging.getLogger("aldwin.core.memory_index")

INDEX_USER_VERSION: Final[int] = 1
INDEX_TEXT_CAP: Final[int] = 4_000
MAX_QUERY_CHARS: Final[int] = 200
MAX_NOTE_HITS: Final[int] = 10
MAX_TURN_HITS: Final[int] = 5
SNIPPET_TOKENS: Final[int] = 24
FTS5_UNAVAILABLE_MSG: Final[str] = (
    "conversation search needs SQLite FTS5, which this Python doesn't have"
)

Scope = Literal["notes", "conversations", "both"]

_NOTE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^(?:n-[0-9a-f]{4}|m_[0-9a-f]{12})$")
_FTS5_AVAILABLE: bool | None = None
_FTS5_FALLBACK_LOGGED: bool = False


def _log_fts5_fallback_once() -> None:
    global _FTS5_FALLBACK_LOGGED
    if not _FTS5_FALLBACK_LOGGED:
        logger.info("FTS5 unavailable; using simple search")
        _FTS5_FALLBACK_LOGGED = True


def synthetic_note_id(file_path: Path, line_text: str) -> str:
    """Stable `n-xxxx` id for a note line that has no explicit id comment."""
    digest = hashlib.sha1(f"{file_path}:{line_text}".encode("utf-8")).hexdigest()
    return f"n-{digest[:4]}"


def build_fts_query(query: str) -> str:
    """Quote each word so FTS5 operators in model input stay literal."""
    words = [w for w in query.split() if w]
    quoted: list[str] = []
    for word in words:
        escaped = word.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " OR ".join(quoted)


class MemoryIndex:
    """Disposable FTS5 cache over `notes_dir` and `sessions_dir`."""

    def __init__(
        self,
        notes_dir: Path,
        sessions_dir: Path,
        index_path: Path,
    ) -> None:
        self.notes_dir = notes_dir
        self.sessions_dir = sessions_dir
        self.index_path = index_path

    @staticmethod
    def fts5_available() -> bool:
        """Return whether this Python's sqlite3 build supports FTS5 porter."""
        global _FTS5_AVAILABLE
        if _FTS5_AVAILABLE is not None:
            return _FTS5_AVAILABLE
        try:
            conn = sqlite3.connect(":memory:")
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE t USING fts5(x, tokenize='porter unicode61')"
                )
                conn.execute("INSERT INTO t(x) VALUES ('running')")
                rows = conn.execute("SELECT x FROM t WHERE t MATCH 'run'").fetchall()
                _FTS5_AVAILABLE = bool(rows)
            finally:
                conn.close()
        except sqlite3.Error:
            _FTS5_AVAILABLE = False
        return _FTS5_AVAILABLE

    def _unlink_index(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.index_path) + suffix)
            path.unlink(missing_ok=True)

    def _ensure_parent(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        if self.fts5_available():
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5("
                "text, note_id UNINDEXED, day UNINDEXED, file UNINDEXED, "
                "tokenize='porter unicode61')"
            )
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS turns USING fts5("
                "text, session_id UNINDEXED, turn UNINDEXED, role UNINDEXED, "
                "day UNINDEXED, tokenize='porter unicode61')"
            )
        else:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS notes("
                "text TEXT, note_id TEXT, day TEXT, file TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS turns("
                "text TEXT, session_id TEXT, turn INTEGER, role TEXT, day TEXT)"
            )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS files("
            "path TEXT PRIMARY KEY, mtime_ns INTEGER, size INTEGER)"
        )
        conn.execute(f"PRAGMA user_version = {INDEX_USER_VERSION}")

    def _connect(self) -> sqlite3.Connection:
        """Open the index, rebuilding on corruption or version mismatch."""
        self._ensure_parent()
        rebuilt = False
        for attempt in range(2):
            try:
                if not self.index_path.exists():
                    rebuilt = True
                conn = sqlite3.connect(str(self.index_path), timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.row_factory = sqlite3.Row
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version != INDEX_USER_VERSION:
                    conn.close()
                    self._unlink_index()
                    rebuilt = True
                    conn = sqlite3.connect(str(self.index_path), timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.row_factory = sqlite3.Row
                    self._init_schema(conn)
                    conn.commit()
                else:
                    # Ensure tables exist even if an empty file was created.
                    tables = {
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type IN "
                            "('table', 'view')"
                        ).fetchall()
                    }
                    if "notes" not in tables or "files" not in tables:
                        self._init_schema(conn)
                        conn.commit()
                try:
                    os.chmod(self.index_path, FILE_MODE)
                except OSError:
                    pass
                if rebuilt and attempt == 0:
                    logger.info("Memory index rebuilt")
                return conn
            except sqlite3.DatabaseError:
                logger.info("Memory index rebuilt")
                self._unlink_index()
                rebuilt = True
                if attempt == 1:
                    raise
        raise sqlite3.DatabaseError("unable to open memory index")

    def sync(self) -> None:
        """Re-index notes and session files whose mtime or size changed."""
        for attempt in range(2):
            try:
                conn = self._connect()
            except sqlite3.DatabaseError:
                if attempt == 0:
                    self._unlink_index()
                    continue
                logger.warning("Could not open memory index for sync")
                return
            try:
                self._sync_notes(conn)
                self._sync_sessions(conn)
                conn.commit()
                return
            except sqlite3.DatabaseError:
                logger.info("Memory index rebuilt")
                self._unlink_index()
            finally:
                try:
                    conn.close()
                except sqlite3.Error:
                    logger.debug("Error closing memory index", exc_info=True)
        logger.warning("Memory index sync failed after rebuild")

    def _tracked_files(self, conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
        rows = conn.execute("SELECT path, mtime_ns, size FROM files").fetchall()
        return {str(r["path"]): (int(r["mtime_ns"]), int(r["size"])) for r in rows}

    def _set_file_meta(
        self,
        conn: sqlite3.Connection,
        path: Path,
        mtime_ns: int,
        size: int,
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO files(path, mtime_ns, size) VALUES (?, ?, ?)",
            (str(path), mtime_ns, size),
        )

    def _delete_file_rows(
        self,
        conn: sqlite3.Connection,
        path: Path,
        kind: str,
    ) -> None:
        key = str(path)
        if kind == "notes":
            conn.execute("DELETE FROM notes WHERE file = ?", (key,))
        else:
            session_id = path.stem
            conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM files WHERE path = ?", (key,))

    def _sync_notes(self, conn: sqlite3.Connection) -> None:
        tracked = self._tracked_files(conn)
        present: set[str] = set()
        if self.notes_dir.is_dir():
            for path in sorted(self.notes_dir.glob("*.md")):
                if not path.is_file() or not DATE_STEM_RE.match(path.stem):
                    continue
                key = str(path)
                present.add(key)
                try:
                    st = path.stat()
                except OSError:
                    continue
                prev = tracked.get(key)
                if prev is not None and prev == (st.st_mtime_ns, st.st_size):
                    continue
                self._delete_file_rows(conn, path, "notes")
                self._index_notes_file(conn, path)
                self._set_file_meta(conn, path, st.st_mtime_ns, st.st_size)

        for key in self._gone(tracked, present, self.notes_dir, self.sessions_dir):
            self._delete_file_rows(conn, Path(key), "notes")

    @staticmethod
    def _gone(
        tracked: dict[str, tuple[int, int]], present: set[str], root: Path, other: Path
    ) -> list[str]:
        """Tracked files under `root` that are no longer on disk.

        Resolving a path costs file-system calls, so only keys that are missing
        and not plainly in the other folder are resolved.
        """
        resolved_root = root.resolve()
        gone: list[str] = []
        for key in tracked:
            if key in present or Path(key).parent == other:
                continue
            try:
                if Path(key).resolve().is_relative_to(resolved_root):
                    gone.append(key)
            except (OSError, ValueError):
                continue
        return gone

    def _index_notes_file(self, conn: sqlite3.Connection, path: Path) -> None:
        day = path.stem
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        seen_ids: set[str] = set()
        for idx, line in enumerate(raw.splitlines(), start=1):
            entry = parse_note_line(line, day, path, line_no=idx)
            if entry is None:
                continue
            note_id = entry.note_id or synthetic_note_id(path, entry.text)
            if note_id in seen_ids:
                logger.debug("Duplicate note id %s in %s; keeping first", note_id, path)
                continue
            seen_ids.add(note_id)
            text = entry.text[:INDEX_TEXT_CAP]
            conn.execute(
                "INSERT INTO notes(text, note_id, day, file) VALUES (?, ?, ?, ?)",
                (text, note_id, day, str(path)),
            )

    def _sync_sessions(self, conn: sqlite3.Connection) -> None:
        tracked = self._tracked_files(conn)
        present: set[str] = set()
        if self.sessions_dir.is_dir():
            for path in sorted(self.sessions_dir.glob("*.json")):
                if not path.is_file() or path.name.startswith("."):
                    continue
                key = str(path)
                present.add(key)
                try:
                    st = path.stat()
                except OSError:
                    continue
                prev = tracked.get(key)
                if prev is not None and prev == (st.st_mtime_ns, st.st_size):
                    continue
                self._delete_file_rows(conn, path, "sessions")
                self._index_session_file(conn, path)
                self._set_file_meta(conn, path, st.st_mtime_ns, st.st_size)

        for key in self._gone(tracked, present, self.sessions_dir, self.notes_dir):
            self._delete_file_rows(conn, Path(key), "sessions")

    def _session_day(self, meta: dict[str, object], path: Path) -> str:
        for field in ("updated_at", "created_at"):
            raw = meta.get(field)
            if isinstance(raw, str) and len(raw) >= 10:
                try:
                    return date.fromisoformat(raw[:10]).isoformat()
                except ValueError:
                    pass
        try:
            return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
        except OSError:
            return date.today().isoformat()

    def _index_session_file(self, conn: sqlite3.Connection, path: Path) -> None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        meta = payload.get("meta")
        raw_memory = payload.get("memory")
        if not isinstance(meta, dict) or not isinstance(raw_memory, str):
            return
        try:
            memory_obj = json.loads(raw_memory)
        except json.JSONDecodeError:
            return
        if not isinstance(memory_obj, dict):
            return
        steps = memory_obj.get("steps")
        if not isinstance(steps, list):
            return

        session_id = str(meta.get("id") or path.stem)
        day = self._session_day(meta, path)
        turn = 0
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_type = step.get("type")
            data = step.get("data")
            if not isinstance(data, dict):
                continue
            if step_type == "task":
                text = data.get("task")
                if isinstance(text, str) and text.strip():
                    turn += 1
                    conn.execute(
                        "INSERT INTO turns(text, session_id, turn, role, day) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (text[:INDEX_TEXT_CAP], session_id, turn, "user", day),
                    )
            elif step_type == "final":
                text = data.get("answer")
                if isinstance(text, str) and text.strip():
                    turn += 1
                    conn.execute(
                        "INSERT INTO turns(text, session_id, turn, role, day) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (text[:INDEX_TEXT_CAP], session_id, turn, "assistant", day),
                    )
            elif step_type == "action":
                model_message = data.get("model_message")
                if isinstance(model_message, dict):
                    text = model_message.get("content")
                    if isinstance(text, str) and text.strip():
                        turn += 1
                        conn.execute(
                            "INSERT INTO turns(text, session_id, turn, role, day) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                text[:INDEX_TEXT_CAP],
                                session_id,
                                turn,
                                "assistant",
                                day,
                            ),
                        )

    def stats(self) -> tuple[int, int, int]:
        """Return `(note_rows, turn_rows, index_bytes)`."""
        try:
            conn = self._connect()
        except sqlite3.DatabaseError:
            return (0, 0, 0)
        try:
            notes = int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
            turns = int(conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0])
            try:
                size = self.index_path.stat().st_size
            except OSError:
                size = 0
            return (notes, turns, size)
        except sqlite3.DatabaseError:
            return (0, 0, 0)
        finally:
            conn.close()

    def lookup_note(self, note_id: str) -> dict[str, str] | None:
        """Return `{note_id, text, day, file}` for `note_id`, or None."""
        self.sync()
        try:
            conn = self._connect()
        except sqlite3.DatabaseError:
            return None
        try:
            row = conn.execute(
                "SELECT text, note_id, day, file FROM notes WHERE note_id = ? LIMIT 1",
                (note_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "note_id": str(row["note_id"]),
                "text": str(row["text"]),
                "day": str(row["day"]),
                "file": str(row["file"]),
            }
        except sqlite3.DatabaseError:
            return None
        finally:
            conn.close()

    def search(
        self,
        query: str,
        *,
        scope: Scope = "both",
        days: int = DEFAULT_RECALL_DAYS,
        today: date | None = None,
    ) -> str:
        """Ranked search over notes and/or conversation turns."""
        if not isinstance(query, str) or not query.strip():
            return "query is empty"
        trimmed = query.strip()[:MAX_QUERY_CHARS]
        if not trimmed:
            return "query is empty"

        self.sync()
        effective_days = max(1, int(days))
        ref_today = today if today is not None else date.today()
        min_day = (ref_today - timedelta(days=effective_days - 1)).isoformat()

        if not self.fts5_available():
            _log_fts5_fallback_once()
            return self._search_fallback(
                trimmed,
                scope=scope,
                min_day=min_day,
                days=effective_days,
            )

        try:
            conn = self._connect()
        except sqlite3.DatabaseError:
            return f'nothing matches "{trimmed}"'

        try:
            fts_q = build_fts_query(trimmed)
            note_lines: list[str] = []
            turn_lines: list[str] = []
            widened = False

            if scope in ("notes", "both"):
                note_lines = self._search_notes_fts(conn, fts_q, min_day=min_day)
            if scope in ("conversations", "both"):
                turn_lines = self._search_turns_fts(conn, fts_q, min_day=min_day)

            # Widen over all time only when the day window returned nothing.
            if not note_lines and not turn_lines:
                if scope in ("notes", "both"):
                    note_lines = self._search_notes_fts(conn, fts_q, min_day=None)
                if scope in ("conversations", "both"):
                    turn_lines = self._search_turns_fts(conn, fts_q, min_day=None)
                widened = bool(note_lines or turn_lines)

            if not note_lines and not turn_lines:
                return f'nothing matches "{trimmed}"'

            parts: list[str] = []
            if widened:
                parts.append(
                    f"(no matches in the last {effective_days} days; showing older)"
                )
            parts.extend(note_lines)
            parts.extend(turn_lines)
            return "\n".join(parts)
        except sqlite3.DatabaseError:
            logger.info("Memory index rebuilt")
            self._unlink_index()
            return f'nothing matches "{trimmed}"'
        finally:
            conn.close()

    def _search_notes_fts(
        self,
        conn: sqlite3.Connection,
        fts_q: str,
        *,
        min_day: str | None,
    ) -> list[str]:
        if min_day is None:
            sql = (
                "SELECT text, note_id, day, bm25(notes) AS rank FROM notes "
                "WHERE notes MATCH ? ORDER BY rank, day DESC LIMIT ?"
            )
            rows = conn.execute(sql, (fts_q, MAX_NOTE_HITS)).fetchall()
        else:
            sql = (
                "SELECT text, note_id, day, bm25(notes) AS rank FROM notes "
                "WHERE notes MATCH ? AND day >= ? "
                "ORDER BY rank, day DESC LIMIT ?"
            )
            rows = conn.execute(sql, (fts_q, min_day, MAX_NOTE_HITS)).fetchall()
        return [
            f"- [{row['day']}] {row['text']}  (id {row['note_id']})" for row in rows
        ]

    def _search_turns_fts(
        self,
        conn: sqlite3.Connection,
        fts_q: str,
        *,
        min_day: str | None,
    ) -> list[str]:
        if min_day is None:
            sql = (
                "SELECT snippet(turns, 0, '', '', '…', ?) AS snip, "
                "session_id, turn, role, day, bm25(turns) AS rank "
                "FROM turns WHERE turns MATCH ? "
                "ORDER BY rank, day DESC LIMIT ?"
            )
            rows = conn.execute(sql, (SNIPPET_TOKENS, fts_q, MAX_TURN_HITS)).fetchall()
        else:
            sql = (
                "SELECT snippet(turns, 0, '', '', '…', ?) AS snip, "
                "session_id, turn, role, day, bm25(turns) AS rank "
                "FROM turns WHERE turns MATCH ? AND day >= ? "
                "ORDER BY rank, day DESC LIMIT ?"
            )
            rows = conn.execute(
                sql, (SNIPPET_TOKENS, fts_q, min_day, MAX_TURN_HITS)
            ).fetchall()
        return [
            (
                f"- [{row['day']}] {row['role']}: {row['snip']}  "
                f"(session {row['session_id']}, turn {row['turn']})"
            )
            for row in rows
        ]

    def _search_fallback(
        self,
        query: str,
        *,
        scope: Scope,
        min_day: str,
        days: int,
    ) -> str:
        if scope == "conversations":
            return FTS5_UNAVAILABLE_MSG
        words = [w for w in query.casefold().split() if w]
        if not words:
            return "query is empty"
        try:
            conn = self._connect()
        except sqlite3.DatabaseError:
            return f'nothing matches "{query}"'
        try:
            rows = conn.execute(
                "SELECT text, note_id, day FROM notes ORDER BY day DESC"
            ).fetchall()
            matches: list[str] = []
            older: list[str] = []
            for row in rows:
                hay = str(row["text"]).casefold()
                if not all(w in hay for w in words):
                    continue
                line = f"- [{row['day']}] {row['text']}  (id {row['note_id']})"
                if str(row["day"]) >= min_day:
                    matches.append(line)
                else:
                    older.append(line)
                if len(matches) >= MAX_NOTE_HITS:
                    break
            if matches:
                return "\n".join(matches[:MAX_NOTE_HITS])
            if older:
                prefix = f"(no matches in the last {days} days; showing older)"
                return "\n".join([prefix, *older[:MAX_NOTE_HITS]])
            if scope == "both":
                # Conversations unavailable without FTS5; notes had nothing.
                return f'nothing matches "{query}"'
            return f'nothing matches "{query}"'
        except sqlite3.DatabaseError:
            return f'nothing matches "{query}"'
        finally:
            conn.close()

    def search_notes_all_time(self, query: str) -> list[dict[str, str]]:
        """Return matching note dicts over all time (for `forget`)."""
        self.sync()
        if not isinstance(query, str) or not query.strip():
            return []
        trimmed = query.strip()[:MAX_QUERY_CHARS]
        try:
            conn = self._connect()
        except sqlite3.DatabaseError:
            return []
        try:
            if self.fts5_available():
                fts_q = build_fts_query(trimmed)
                rows = conn.execute(
                    "SELECT text, note_id, day, file, bm25(notes) AS rank "
                    "FROM notes WHERE notes MATCH ? "
                    "ORDER BY rank, day DESC LIMIT 50",
                    (fts_q,),
                ).fetchall()
            else:
                words = [w for w in trimmed.casefold().split() if w]
                rows = []
                for row in conn.execute(
                    "SELECT text, note_id, day, file FROM notes ORDER BY day DESC"
                ).fetchall():
                    hay = str(row["text"]).casefold()
                    if all(w in hay for w in words):
                        rows.append(row)
            return [
                {
                    "note_id": str(r["note_id"]),
                    "text": str(r["text"]),
                    "day": str(r["day"]),
                    "file": str(r["file"]),
                }
                for r in rows
            ]
        except sqlite3.DatabaseError:
            return []
        finally:
            conn.close()


def default_memory_index(home: Path) -> MemoryIndex:
    """Build a `MemoryIndex` rooted at an Aldwin home directory."""
    return MemoryIndex(
        notes_dir=home / "memory" / "notes",
        sessions_dir=home / "sessions",
        index_path=home / "memory" / "index.sqlite",
    )


def format_index_size(num_bytes: int) -> str:
    """Human-readable index size for `/memory` footer."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def is_note_id_query(query: str) -> bool:
    """True when `query` looks like a note id (`n-xxxx` or `m_…`)."""
    return bool(_NOTE_ID_RE.match(query.strip()))


__all__ = [
    "FTS5_UNAVAILABLE_MSG",
    "MemoryIndex",
    "build_fts_query",
    "default_memory_index",
    "format_index_size",
    "is_note_id_query",
    "synthetic_note_id",
]
