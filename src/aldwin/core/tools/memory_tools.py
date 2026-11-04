"""Memory tools: remember, recall, and forget."""

from __future__ import annotations

import asyncio
import difflib
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.home import home_dir
from aldwin.core.memory import (
    DEFAULT_RECALL_DAYS,
    MAX_NOTE_CHARS,
    delete_note_by_id,
    find_note_by_id,
    remember_note,
    replace_note_text,
    sanitize_note_text,
)
from aldwin.core.memory_index import (
    default_memory_index,
    is_note_id_query,
)
from aldwin.core.policy import ApprovalPreview, looks_like_secret
from aldwin.core.tools import ToolOutput

MEMORY_TOOL_NAMES: Final[frozenset[str]] = frozenset({"remember", "recall", "forget"})
_SECRET_REFUSAL: Final[str] = (
    "that looks like a password, key or card number; Aldwin doesn't store secrets"
)
_OBJECT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
}
Scope = Literal["notes", "conversations", "both"]


def running_summary(
    name: str,
    arguments: dict[str, Any] | None,
    workspace: Any = None,
) -> str:
    """Pending row for memory tools, or `\"\"` for other tools."""
    del workspace
    if name not in MEMORY_TOOL_NAMES:
        return ""
    args = arguments or {}
    if name == "recall":
        query = args.get("query", "")
        shown = " ".join(str(query).split())
        if len(shown) > 40:
            shown = f"{shown[:39]}…"
        return f'Recall "{shown}"'
    if name == "forget":
        return "Forget"
    if name == "remember" and args.get("replace"):
        return "Remember"
    return ""


def _home(path: Path | None) -> Path:
    return (path if path is not None else home_dir()).expanduser().resolve()


def _refuse_secrets(text: str) -> None:
    if looks_like_secret(text):
        raise ToolCallError(_SECRET_REFUSAL)


def _count_hits(result: str) -> tuple[int, int]:
    notes = 0
    conversations = 0
    for line in result.splitlines():
        if not line.startswith("- ["):
            continue
        if "(session " in line and ", turn " in line:
            conversations += 1
        elif "(id " in line:
            notes += 1
    return notes, conversations


def _recall_summary(query: str, result: str) -> str:
    shown = " ".join(query.split())
    if len(shown) > 40:
        shown = f"{shown[:39]}…"
    if result.startswith("query is empty") or result.startswith("nothing matches"):
        return f'Recall "{shown}" · 0 notes'
    if "conversation search needs SQLite FTS5" in result:
        return f'Recall "{shown}" · conversations unavailable'
    notes, conversations = _count_hits(result)
    parts: list[str] = []
    if notes:
        parts.append(f"{notes} note" + ("s" if notes != 1 else ""))
    if conversations:
        parts.append(
            f"{conversations} conversation" + ("s" if conversations != 1 else "")
        )
    if not parts:
        parts.append("0 notes")
    return f'Recall "{shown}" · ' + ", ".join(parts)


def _one_line_diff(old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            [old + "\n"],
            [new + "\n"],
            fromfile="a/note",
            tofile="b/note",
            n=0,
        )
    )


@dataclass(frozen=True, slots=True)
class _ReplaceState:
    note_id: str
    day: str
    path: Path
    old_text: str
    new_text: str
    diff: str
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class _ForgetState:
    note_id: str
    day: str
    path: Path
    text: str
    detail: str
    mtime_ns: int
    summary: str


class RememberTool(Tool):
    """Append a note or replace an existing note after Diff approval."""

    name = "remember"
    description = (
        "Save a short durable fact (1-500 characters). Pass replace=<note_id> to "
        "update an existing note in place (shows a diff for approval). Never store "
        "secrets or API keys."
    )
    parallel_safe = False
    timeout: float | None = None
    max_output_chars = 4_000
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "Single-line fact to remember (1-500 characters). "
                    "Never pass API keys, passwords, or secrets."
                ),
            },
            "replace": {
                "type": "string",
                "description": "Optional note id to update in place (from recall).",
            },
        },
        "required": ["text"],
    }

    def __init__(
        self,
        home: Path | None = None,
        *,
        session_context: Any = None,
        session_id_getter: Callable[[], str] | None = None,
    ) -> None:
        self._home = home
        self._session_context = session_context
        self._session_id_getter = session_id_getter

    def _prepare_replace(self, text: str, replace: str) -> _ReplaceState:
        _refuse_secrets(text)
        cleaned = sanitize_note_text(text)
        if not cleaned:
            raise ToolCallError(
                f"Invalid note: text must be between 1 and {MAX_NOTE_CHARS} characters."
            )
        if len(cleaned) > MAX_NOTE_CHARS:
            raise ToolCallError(f"note too long (limit {MAX_NOTE_CHARS} characters)")

        root = _home(self._home)
        idx = default_memory_index(root)
        idx.sync()
        note_id = replace.strip()
        found = find_note_by_id(root, note_id)
        if found is None:
            raise ToolCallError(f"no note with id {note_id}")
        path, _line_idx, entry = found
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError as exc:
            raise ToolCallError(f"no note with id {note_id}") from exc
        diff = _one_line_diff(entry.text, cleaned)
        return _ReplaceState(
            note_id=note_id,
            day=entry.date,
            path=path,
            old_text=entry.text,
            new_text=cleaned,
            diff=diff,
            mtime_ns=mtime_ns,
        )

    def prepare_approval(
        self,
        text: str,
        replace: str | None = None,
    ) -> ApprovalPreview:
        """Build Diff preview for `replace`; validate-only for new notes."""
        if replace is None or str(replace).strip() == "":
            _refuse_secrets(text)
            cleaned = sanitize_note_text(text)
            if not cleaned:
                raise ToolCallError(
                    f"Invalid note: text must be between 1 and {MAX_NOTE_CHARS} "
                    "characters."
                )
            if len(cleaned) > MAX_NOTE_CHARS:
                raise ToolCallError(
                    f"note too long (limit {MAX_NOTE_CHARS} characters)"
                )
            return ApprovalPreview(
                summary="Save note",
                detail=cleaned,
                mtime_ns=None,
            )
        state = self._prepare_replace(text, str(replace))
        return ApprovalPreview(
            summary=f"Update note ({state.day})",
            detail=state.diff,
            mtime_ns=state.mtime_ns,
        )

    async def forward(  # type: ignore[override]
        self,
        text: str,
        replace: str | None = None,
    ) -> ToolOutput:
        started = time.monotonic()
        if replace is not None and str(replace).strip() != "":
            state = self._prepare_replace(text, str(replace))
            try:
                status = await asyncio.to_thread(
                    replace_note_text,
                    self._home,
                    state.note_id,
                    state.new_text,
                )
            except ValueError as exc:
                raise ToolCallError(str(exc)) from exc
            default_memory_index(_home(self._home)).sync()
            elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
            return ToolOutput(
                status,
                summary="Remember · updated",
                ok=True,
                duration_ms=elapsed_ms,
            )

        _refuse_secrets(text)
        cleaned = sanitize_note_text(text)
        if len(cleaned) > MAX_NOTE_CHARS:
            raise ToolCallError(f"note too long (limit {MAX_NOTE_CHARS} characters)")
        if self._session_context is not None and getattr(
            self._session_context, "session_id", None
        ):
            session_id = str(self._session_context.session_id)
        elif self._session_id_getter is not None:
            session_id = self._session_id_getter()
        else:
            session_id = "default"
        try:
            status = await asyncio.to_thread(
                remember_note,
                self._home,
                text,
                session_id=session_id,
            )
        except ValueError as exc:
            msg = str(exc)
            return ToolOutput(msg, summary=msg, ok=False)
        default_memory_index(_home(self._home)).sync()
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        summary = "Remember · saved" if status == "saved" else status
        return ToolOutput(status, summary=summary, ok=True, duration_ms=elapsed_ms)


class RecallTool(Tool):
    """Ranked FTS5 search over notes and saved conversations."""

    name = "recall"
    description = (
        "Search saved memory notes and past conversations. "
        "scope: notes | conversations | both (default both)."
    )
    parallel_safe = True
    timeout = 10.0
    max_output_chars = 8_000
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "query": {
                "type": "string",
                "description": "Words to search for in notes and/or conversations.",
            },
            "scope": {
                "type": "string",
                "enum": ["notes", "conversations", "both"],
                "description": "Where to search (default both).",
                "default": "both",
            },
            "days": {
                "type": "integer",
                "description": "Recent days window before widening (default 30).",
                "default": DEFAULT_RECALL_DAYS,
            },
        },
        "required": ["query"],
    }

    def __init__(self, home: Path | None = None) -> None:
        self._home = home

    async def forward(  # type: ignore[override]
        self,
        query: str,
        scope: Scope = "both",
        days: int = DEFAULT_RECALL_DAYS,
    ) -> ToolOutput:
        started = time.monotonic()
        if scope not in ("notes", "conversations", "both"):
            scope = "both"
        idx = default_memory_index(_home(self._home))
        result = await asyncio.to_thread(
            idx.search,
            query,
            scope=scope,
            days=days,
        )
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return ToolOutput(
            result,
            summary=_recall_summary(str(query), result),
            ok=True,
            duration_ms=elapsed_ms,
        )


class ForgetTool(Tool):
    """Delete a memory note after Diff approval."""

    name = "forget"
    description = (
        "Forget a saved note. Pass a search query or note id. "
        "When several notes match, returns the list so you can call again with an id."
    )
    parallel_safe = False
    timeout = None
    max_output_chars = 4_000
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "query": {
                "type": "string",
                "description": "Note id (n-xxxx / m_…) or words matching one note.",
            },
        },
        "required": ["query"],
    }

    def __init__(self, home: Path | None = None) -> None:
        self._home = home

    def _resolve_single(self, query: str) -> _ForgetState | str:
        """Return forget state, a multi-match message, or raise ToolCallError."""
        root = _home(self._home)
        idx = default_memory_index(root)
        idx.sync()
        q = query.strip()
        matches: list[dict[str, str]]
        if is_note_id_query(q):
            hit = idx.lookup_note(q)
            # lookup_note syncs again; also try file lookup for synthetic ids.
            if hit is None:
                found = find_note_by_id(root, q)
                if found is None:
                    return f'no note matches "{q}"'
                path, _idx, entry = found
                matches = [
                    {
                        "note_id": q,
                        "text": entry.text,
                        "day": entry.date,
                        "file": str(path),
                    }
                ]
            else:
                matches = [hit]
        else:
            matches = idx.search_notes_all_time(q)

        if not matches:
            return f'no note matches "{q}"'
        if len(matches) > 1:
            lines = [
                f"- [{m['day']}] {m['text']}  (id {m['note_id']})" for m in matches
            ]
            lines.append(
                "several notes match; call forget with the id of the one to remove"
            )
            return "\n".join(lines)

        m = matches[0]
        path = Path(m["file"])
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            # Fall back via find_note_by_id.
            found = find_note_by_id(root, m["note_id"])
            if found is None:
                return f'no note matches "{q}"'
            path, _i, entry = found
            mtime_ns = path.stat().st_mtime_ns
            m = {
                "note_id": m["note_id"],
                "text": entry.text,
                "day": entry.date,
                "file": str(path),
            }
        preview = m["text"]
        if len(preview) > 120:
            preview = preview[:117] + "…"
        summary = f'Forget this note? "{preview}" ({m["day"]})'
        detail = f"- [{m['day']}] {m['text']}  (id {m['note_id']})"
        return _ForgetState(
            note_id=m["note_id"],
            day=m["day"],
            path=path,
            text=m["text"],
            detail=detail,
            mtime_ns=mtime_ns,
            summary=summary,
        )

    def prepare_approval(self, query: str) -> ApprovalPreview:
        """Ask only when exactly one note matches; otherwise raise with the message."""
        resolved = self._resolve_single(query)
        if isinstance(resolved, str):
            # Zero or many matches: return the list without asking (forget never
            # Diff-asks then). ToolCallError carries the informational text so
            # the model sees the ids and the "call forget with the id…" line.
            raise ToolCallError(resolved)
        return ApprovalPreview(
            summary=resolved.summary,
            detail=resolved.detail,
            mtime_ns=resolved.mtime_ns,
        )

    async def forward(self, query: str) -> ToolOutput:  # type: ignore[override]
        started = time.monotonic()
        # Re-resolve after approval (mtime recheck already re-ran prepare).
        resolved = self._resolve_single(query)
        if isinstance(resolved, str):
            raise ToolCallError(resolved)
        try:
            status = await asyncio.to_thread(
                delete_note_by_id,
                self._home,
                resolved.note_id,
            )
        except ValueError as exc:
            raise ToolCallError(str(exc)) from exc
        default_memory_index(_home(self._home)).sync()
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        return ToolOutput(
            status,
            summary="Forget · 1 note removed",
            ok=True,
            duration_ms=elapsed_ms,
        )


def build_memory_tools(
    home: Path | None = None,
    *,
    session_context: Any = None,
    session_id_getter: Callable[[], str] | None = None,
) -> tuple[RememberTool, RecallTool, ForgetTool]:
    """Construct `(RememberTool, RecallTool, ForgetTool)` for `home`."""
    return (
        RememberTool(
            home=home,
            session_context=session_context,
            session_id_getter=session_id_getter,
        ),
        RecallTool(home=home),
        ForgetTool(home=home),
    )


__all__ = [
    "ForgetTool",
    "MEMORY_TOOL_NAMES",
    "RecallTool",
    "RememberTool",
    "build_memory_tools",
    "running_summary",
]
