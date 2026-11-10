"""Session persistence, resume, and history rendering for the Aldwin host."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

from agentling.errors import MemoryLoadError
from agentling.memory import ActionStep, FinalStep, Memory, TaskStep

from aldwin.core.home import FILE_MODE, atomic_write, home_dir
from aldwin.protocol import HistoryItem

logger = logging.getLogger("aldwin.host.sessions")

SESSIONS_SUBDIR: Final[str] = "sessions"
MAX_TITLE_CHARS: Final[int] = 60
MAX_PREVIEW_CHARS: Final[int] = 120
# Ids are `<YYYY-MM-DD>_<hex>` file stems; anything else could escape the folder.
_SESSION_ID_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _local_date_str() -> str:
    """Return today's local date as `YYYY-MM-DD`."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def shorten_one_line(text: str, limit: int = MAX_PREVIEW_CHARS) -> str:
    """Collapse whitespace/newlines to single spaces and truncate to `limit` chars."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def derive_session_title(
    memory: Memory,
    turns: list[dict[str, Any]] | None = None,
) -> str:
    """Derive a session title from the first user message (up to 60 characters).

    Prefer original typed text from `turns` so `@`-injected task markup never
    becomes the session title.
    """
    if turns:
        first = turns[0].get("text")
        if isinstance(first, str) and first.strip():
            normalised = " ".join(first.split())
            return normalised[:MAX_TITLE_CHARS]
    for step in memory.steps:
        if isinstance(step, TaskStep):
            normalised = " ".join(step.task.split())
            if normalised:
                return normalised[:MAX_TITLE_CHARS]
    return ""


def _is_memory_interrupted(memory: Memory) -> bool:
    """Return `True` when a non-empty memory does not end with a `FinalStep`."""
    return bool(memory.steps) and not isinstance(memory.steps[-1], FinalStep)


@dataclass
class SessionMeta:
    """Metadata stored alongside a serialized Agentling `Memory`."""

    id: str
    created_at: str
    updated_at: str
    model: str
    title: str
    interrupted: bool


@dataclass(frozen=True)
class SessionMetaSummary:
    """One session's metadata, read without loading its conversation."""

    id: str
    created_at: str
    updated_at: str
    model: str
    title: str
    interrupted: bool
    has_turns: bool


def session_has_turns(turns: Any, title: str) -> bool:
    """Return whether a session holds at least one user turn.

    Files written before `turns` was stored have none; a non-empty title then
    stands in, because the title is only set from the first message.
    """
    if _normalise_turns_payload(turns):
        return True
    return bool(title)


def session_file_fingerprint(path: Path) -> tuple[int, int]:
    """Return ``(mtime_ns, size)`` of a session file, or ``(0, 0)`` if missing."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (0, 0)
    return (stat.st_mtime_ns, stat.st_size)


def _full_model_name(raw_model: str) -> str:
    return raw_model if "/" in raw_model else f"openai/{raw_model}"


@dataclass
class StoredSession:
    """An on-disk conversation session loaded or managed by `SessionStore`."""

    path: Path
    meta: SessionMeta
    memory: Memory
    todo: list[dict[str, str]] | None = None
    # Parallel to TaskSteps: original user text + resolved skill names (not bodies).
    turns: list[dict[str, Any]] | None = None


def _normalise_todo_payload(raw: Any) -> list[dict[str, str]]:
    """Parse a session `todo` value into a list of `{text, status}` dicts."""
    if not isinstance(raw, list):
        return []
    items: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return []
        text = entry.get("text")
        status = entry.get("status", "pending")
        if not isinstance(text, str) or not isinstance(status, str):
            return []
        items.append({"text": text, "status": status})
    return items


def _normalise_turns_payload(raw: Any) -> list[dict[str, Any]]:
    """Parse session `turns` into `[{text, skills}]` (skills names only)."""
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return []
        text = entry.get("text")
        skills = entry.get("skills", [])
        if not isinstance(text, str):
            return []
        if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
            return []
        items.append({"text": text, "skills": list(skills)})
    return items


class SessionStore:
    """Manages `~/.aldwin/sessions/<YYYY-MM-DD>_<shortid>.json` files."""

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self._sessions_dir = sessions_dir

    @property
    def sessions_dir(self) -> Path:
        """Resolve the active `sessions/` directory dynamically via `home_dir()`."""
        if self._sessions_dir is not None:
            return self._sessions_dir.expanduser().resolve()
        return home_dir() / SESSIONS_SUBDIR

    def create_new(self, model: str = "gpt-4o") -> StoredSession:
        """Create and persist a fresh empty session file without touching old ones."""
        now = _utc_now_iso()
        session_id = f"{_local_date_str()}_{uuid4().hex[:8]}"
        path = self.sessions_dir / f"{session_id}.json"
        full_model = model if "/" in model else f"openai/{model}"
        meta = SessionMeta(
            id=session_id,
            created_at=now,
            updated_at=now,
            model=full_model,
            title="",
            interrupted=False,
        )
        memory = Memory()
        stored = StoredSession(path=path, meta=meta, memory=memory)
        self.save(stored, memory, interrupted=False)
        return stored

    def save(
        self,
        stored: StoredSession,
        memory: Memory,
        *,
        interrupted: bool,
        todo: list[dict[str, str]] | None = None,
        touch: bool = True,
    ) -> None:
        """Atomically persist `stored` and `memory` to `stored.path` (mode `0600`).

        `touch=False` keeps `updated_at`, so saving a conversation the user is
        only leaving does not move it to the top of the history list.
        """
        stored.memory = memory
        if touch:
            stored.meta.updated_at = _utc_now_iso()
        stored.meta.interrupted = interrupted
        if todo is not None:
            stored.todo = list(todo)
        if not stored.meta.title:
            stored.meta.title = derive_session_title(memory, stored.turns)

        payload: dict[str, Any] = {
            "meta": asdict(stored.meta),
            "memory": memory.dump_json(),
        }
        if stored.todo:
            payload["todo"] = stored.todo
        if stored.turns:
            payload["turns"] = stored.turns
        serialized = json.dumps(payload, indent=2) + "\n"
        atomic_write(stored.path, serialized, mode=FILE_MODE)

    def _session_files(self) -> list[Path]:
        directory = self.sessions_dir
        if not directory.exists():
            return []
        return [
            p
            for p in directory.glob("*.json")
            if p.is_file() and not p.name.startswith(".")
        ]

    def load_latest(self, default_model: str = "gpt-4o") -> StoredSession:
        """Load the most recently updated valid session, or create a new one."""
        candidates = self._session_files()
        if not candidates:
            return self.create_new(model=default_model)

        loaded_sessions: list[tuple[str, int, StoredSession]] = []
        for path in candidates:
            stored = self._try_load_file(path)
            if stored is not None:
                mtime_ns = path.stat().st_mtime_ns
                loaded_sessions.append((stored.meta.updated_at, mtime_ns, stored))

        if not loaded_sessions:
            return self.create_new(model=default_model)

        loaded_sessions.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return loaded_sessions[0][2]

    def list_metas(self, *, include_empty: bool = False) -> list[SessionMetaSummary]:
        """Return session metadata newest first, skipping unreadable files.

        Only `meta` and `turns` are parsed, never the conversation, so listing
        stays fast. Sessions with no turns are left out unless `include_empty`.
        """
        found: list[tuple[str, int, SessionMetaSummary]] = []
        for path in self._session_files():
            summary = self._try_read_meta(path)
            if summary is None or not (include_empty or summary.has_turns):
                continue
            found.append((summary.updated_at, path.stat().st_mtime_ns, summary))
        found.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [summary for _, _, summary in found]

    def load_id(self, session_id: str) -> StoredSession | None:
        """Load the session saved as `<session_id>.json`, or `None`.

        Returns `None` for an id that is not a plain file name, a missing file,
        a corrupt file, or a file whose stored id differs.
        """
        if not _SESSION_ID_RE.fullmatch(session_id):
            return None
        path = self.sessions_dir / f"{session_id}.json"
        if not path.is_file():
            return None
        stored = self._try_load_file(path)
        if stored is None or stored.meta.id != session_id:
            return None
        return stored

    def _try_read_meta(self, path: Path) -> SessionMetaSummary | None:
        try:
            raw_obj: Any = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw_obj, dict):
                raise ValueError("Root must be a JSON object.")
            raw_meta = raw_obj.get("meta")
            if not isinstance(raw_meta, dict) or not isinstance(
                raw_obj.get("memory"), str
            ):
                raise ValueError("Missing 'meta' object or 'memory' string.")
            title = str(raw_meta.get("title", ""))
            return SessionMetaSummary(
                id=str(raw_meta["id"]),
                created_at=str(raw_meta["created_at"]),
                updated_at=str(raw_meta["updated_at"]),
                model=_full_model_name(str(raw_meta["model"])),
                title=title,
                interrupted=bool(raw_meta.get("interrupted", False)),
                has_turns=session_has_turns(raw_obj.get("turns"), title),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping unreadable session file %s: %s", path, exc)
            return None

    def load_file(self, path: Path) -> StoredSession | None:
        """Parse one session file, returning `None` if corrupt."""
        return self._try_load_file(path)

    def _try_load_file(self, path: Path) -> StoredSession | None:
        """Parse one session file, logging a warning and returning `None` if corrupt."""
        try:
            raw_obj: Any = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw_obj, dict):
                raise ValueError("Root must be a JSON object.")

            raw_meta = raw_obj.get("meta")
            raw_memory = raw_obj.get("memory")
            if not isinstance(raw_meta, dict) or not isinstance(raw_memory, str):
                raise ValueError("Missing 'meta' object or 'memory' string.")

            memory = Memory.load_json(raw_memory)
            if "interrupted" in raw_meta:
                interrupted = bool(raw_meta["interrupted"])
            else:
                interrupted = _is_memory_interrupted(memory)
            turns = _normalise_turns_payload(raw_obj.get("turns"))
            meta = SessionMeta(
                id=str(raw_meta["id"]),
                created_at=str(raw_meta["created_at"]),
                updated_at=str(raw_meta["updated_at"]),
                model=_full_model_name(str(raw_meta["model"])),
                title=str(raw_meta.get("title", ""))
                or derive_session_title(memory, turns or None),
                interrupted=interrupted,
            )
            todo = _normalise_todo_payload(raw_obj.get("todo"))
            return StoredSession(
                path=path,
                meta=meta,
                memory=memory,
                todo=todo or None,
                turns=turns or None,
            )
        except (
            OSError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            MemoryLoadError,
        ) as exc:
            logger.warning("Skipping unreadable session file %s: %s", path, exc)
            return None

    def render_history(
        self,
        memory: Memory,
        limit: int = 20,
        turns: list[dict[str, Any]] | None = None,
    ) -> list[HistoryItem]:
        """Render the last `limit` user/assistant messages with tool summaries.

        When `turns` is provided, user rows use the original typed text instead
        of any `<picked_skills>` injection stored in Agentling `TaskStep.task`.
        """
        if limit < 1:
            return []

        items: list[HistoryItem] = []
        turn_idx = 0
        for step in memory.steps:
            if isinstance(step, TaskStep):
                text = step.task
                if turns is not None and turn_idx < len(turns):
                    stored = turns[turn_idx].get("text")
                    if isinstance(stored, str):
                        text = stored
                turn_idx += 1
                items.append(HistoryItem(role="user", text=text))
            elif isinstance(step, ActionStep):
                for result in step.tool_results:
                    if result.name == "final_answer" and not result.is_error:
                        continue
                    prefix = "✗" if result.is_error else "✓"
                    first_line = (
                        result.content.splitlines()[0] if result.content else ""
                    )
                    summary = shorten_one_line(first_line, MAX_PREVIEW_CHARS)
                    line = (
                        f"{prefix} {result.name} · {summary}"
                        if summary
                        else f"{prefix} {result.name}"
                    )
                    items.append(HistoryItem(role="tool", text=line))
            elif isinstance(step, FinalStep):
                items.append(HistoryItem(role="assistant", text=step.answer))

        ua_indices = [
            idx for idx, item in enumerate(items) if item.role in ("user", "assistant")
        ]
        if len(ua_indices) > limit:
            start_idx = ua_indices[-limit]
            return items[start_idx:]
        return items
