"""Diff-tier file edit tools: `edit_file` and `write_file`."""

from __future__ import annotations

import difflib
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.policy import ApprovalPreview, PolicySession
from aldwin.core.tools import ToolOutput
from aldwin.core.tools import display as core_display
from aldwin.core.workspace import Workspace, WorkspaceError, format_outside_display

MAX_WRITE_BYTES: Final[int] = 1_048_576
EDIT_TOOL_NAMES: Final[frozenset[str]] = frozenset({"edit_file", "write_file"})
_MINUS: Final[str] = "\u2212"  # typographic minus used in approval summaries
_UTF8_BOM: Final[bytes] = b"\xef\xbb\xbf"

_OBJECT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
}


def detect_newline(text: str) -> str:
    """Return `\\r\\n` when at least half of the line breaks are CRLF, else `\\n`."""
    crlf = text.count("\r\n")
    lf_only = text.count("\n") - crlf
    total = crlf + lf_only
    if total == 0:
        return "\n"
    return "\r\n" if crlf * 2 >= total else "\n"


def normalize_newlines(text: str, nl: str) -> str:
    """Rewrite every line break in `text` to `nl`."""
    unified = text.replace("\r\n", "\n").replace("\r", "\n")
    if nl == "\n":
        return unified
    return unified.replace("\n", "\r\n")


def closest_lines(
    haystack: str,
    needle: str,
    *,
    n: int = 3,
) -> list[tuple[int, str]]:
    """Return up to `n` `(line_number, line)` pairs most similar to `needle`."""
    lines = haystack.splitlines()
    if not lines:
        return []
    scored: list[tuple[float, int, str]] = []
    for index, line in enumerate(lines, start=1):
        ratio = difflib.SequenceMatcher(None, needle, line).ratio()
        scored.append((ratio, index, line))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(index, line) for _ratio, index, line in scored[:n]]


def count_plus_minus(diff: str) -> tuple[int, int]:
    """Count added and removed lines in a unified diff (excluding file headers)."""
    added = 0
    removed = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def unified_edit_diff(relpath: str, before: str, after: str) -> str:
    """Build a unified diff with 3 lines of context for an in-place edit."""
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{relpath}",
            tofile=f"b/{relpath}",
            n=3,
        )
    )


def unified_create_diff(relpath: str, content: str) -> str:
    """Build an all-added unified diff for a new file (`--- /dev/null`)."""
    after_lines = content.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            [],
            after_lines,
            fromfile="/dev/null",
            tofile=f"b/{relpath}",
            n=3,
        )
    )


def atomic_write_workspace(
    path: Path,
    data: bytes,
    *,
    preserve_from: Path | None,
) -> None:
    """Write `data` to `path` via temp file, fsync, optional `copymode`, and replace.

    The caller creates parent directories. On any failure the temp file is removed and
    the original target (if any) is left untouched. Custom xattrs and ACLs are not
    copied across `os.replace` on macOS APFS.
    """
    parent = path.parent
    tmp_path = parent / f".{path.name}.aldwin-tmp"
    try:
        with tmp_path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if preserve_from is not None and preserve_from.exists():
            shutil.copymode(preserve_from, tmp_path)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _display_path(path: Path, workspace: Workspace) -> str:
    try:
        relative = path.resolve().relative_to(workspace.root.resolve())
        return relative.as_posix() or "."
    except ValueError:
        return format_outside_display(path)


def _display_arg(workspace: Workspace, raw_path: str) -> str:
    try:
        return workspace.resolve_safe(raw_path).display
    except (WorkspaceError, OSError, ValueError):
        return raw_path


def running_summary(
    name: str,
    arguments: dict[str, Any] | None,
    workspace: Workspace,
) -> str:
    """One-line pending row for an edit tool, or `\"\"` for any other tool."""
    if name not in EDIT_TOOL_NAMES:
        return ""
    args = arguments or {}
    raw_path = args.get("path", "")
    shown = raw_path if isinstance(raw_path, str) else str(raw_path)
    display = _display_arg(workspace, shown)
    if name == "edit_file":
        return f"Edit {display}"
    return f"Write {display}"


def row_summary(running: str, summary: str, *, ok: bool) -> str:
    """Rewrite a denial row for edit tools. Success summaries stay as written."""
    if ok or not running:
        return summary
    lower = summary.lower()
    if summary == "declined" or "denied by the user" in lower:
        return f"{running} · declined"
    if summary == "not run: no approver" or "no one was available to approve" in lower:
        return f"{running} · not run: no one to approve"
    if (summary.startswith("blocked:") and "secret" in lower) or (
        "blocked because it may hold secrets" in lower
    ):
        return f"{running} · blocked: may contain secrets"
    if (summary.startswith("blocked:") and "inside .git" in summary) or (
        "is inside .git and cannot be modified" in lower
    ):
        if summary.startswith("blocked:"):
            return f"{running} · {summary}"
        return f"{running} · blocked: inside .git"
    return summary


def _line_count(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def _match_line_numbers(text: str, needle: str) -> list[int]:
    lines: list[int] = []
    start = 0
    step = max(len(needle), 1)
    while True:
        index = text.find(needle, start)
        if index < 0:
            break
        lines.append(text.count("\n", 0, index) + 1)
        start = index + step
    return lines


def _read_text_file(path: Path) -> tuple[str, bool, str]:
    """Return `(text, had_bom, newline)`. Raises `ToolCallError` on bad encoding."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ToolCallError(f"failed to read {path.name}: {exc}") from exc
    had_bom = raw.startswith(_UTF8_BOM)
    body = raw[len(_UTF8_BOM) :] if had_bom else raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolCallError(
            "unsupported encoding; edit with run_command instead"
        ) from exc
    return text, had_bom, detect_newline(text)


def _encode_text(text: str, *, had_bom: bool) -> bytes:
    data = text.encode("utf-8")
    if had_bom:
        return _UTF8_BOM + data
    return data


def _require_read(
    session: PolicySession,
    resolved: Path,
    display: str,
) -> int:
    recorded = session.read_files.get(resolved)
    if recorded is None:
        raise ToolCallError(f"read the file first: {display}")
    try:
        current = resolved.stat().st_mtime_ns
    except OSError as exc:
        raise ToolCallError(f"failed to stat {display}: {exc}") from exc
    if current != recorded:
        raise ToolCallError("file changed since last read; read it again")
    return current


def _missing_parent_displays(resolved: Path, workspace: Workspace) -> list[str]:
    missing: list[str] = []
    current = resolved.parent
    root = workspace.root.resolve()
    while not current.exists() and current != current.parent:
        try:
            rel = current.resolve(strict=False).relative_to(root)
            missing.append(f"{rel.as_posix()}/")
        except ValueError:
            missing.append(f"{current.as_posix()}/")
        current = current.parent
    missing.reverse()
    return missing


def _plus_minus_clause(added: int, removed: int) -> str:
    return f"(+{added} {_MINUS}{removed})"


@dataclass(frozen=True, slots=True)
class _EditState:
    resolved: Path
    display: str
    before: str
    after: str
    diff: str
    added: int
    removed: int
    mtime_ns: int
    had_bom: bool
    inside: bool


@dataclass(frozen=True, slots=True)
class _WriteState:
    resolved: Path
    display: str
    content: str
    diff: str
    summary: str
    mtime_ns: int | None
    is_new: bool
    had_bom: bool
    added: int
    removed: int
    line_count: int
    creates: list[str]
    inside: bool


class EditFileTool(Tool):
    """Exact-string file edit with Diff approval."""

    name = "edit_file"
    description = (
        "Replace one unique exact string in a text file. The file must have been "
        "read in this session and unchanged since. Shows a unified diff for approval. "
        "Changes cannot be undone automatically; describe what you will change first."
    )
    parallel_safe = False
    timeout = None
    max_output_chars = 4_000
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "path": {
                "type": "string",
                "description": "File to edit, relative to the workspace.",
            },
            "old": {
                "type": "string",
                "description": "Exact text to find. Must match once.",
            },
            "new": {
                "type": "string",
                "description": "Replacement text.",
            },
        },
        "required": ["path", "old", "new"],
    }

    def __init__(self, workspace: Workspace, policy_session: PolicySession) -> None:
        self._workspace = workspace
        self._session = policy_session

    def _prepare(self, path: str, old: str, new: str) -> _EditState:
        if not isinstance(old, str) or old == "":
            raise ToolCallError("old is empty")
        if not isinstance(new, str):
            raise ToolCallError("new must be a string")
        if old == new:
            raise ToolCallError("old and new must differ")

        try:
            resolved_info = self._workspace.resolve_safe(
                path,
                granted_folders=self._session.granted_folders,
            )
        except WorkspaceError as exc:
            raise ToolCallError(str(exc)) from exc

        resolved = resolved_info.path
        display = resolved_info.display
        mtime_ns = _require_read(self._session, resolved, display)
        before, had_bom, newline = _read_text_file(resolved)
        old_n = normalize_newlines(old, newline)
        new_n = normalize_newlines(new, newline)
        matches = _match_line_numbers(before, old_n)
        if len(matches) == 0:
            closest = closest_lines(before, old_n, n=3)
            lines = "\n".join(f"{number}: {line}" for number, line in closest)
            raise ToolCallError(f"no match for old; closest lines:\n{lines}")
        if len(matches) > 1:
            shown = ", ".join(str(number) for number in matches[:10])
            raise ToolCallError(
                f"matches {len(matches)} times (lines {shown}); add more context"
            )

        after = before.replace(old_n, new_n, 1)
        diff = unified_edit_diff(display, before, after)
        added, removed = count_plus_minus(diff)
        return _EditState(
            resolved=resolved,
            display=display,
            before=before,
            after=after,
            diff=diff,
            added=added,
            removed=removed,
            mtime_ns=mtime_ns,
            had_bom=had_bom,
            inside=resolved_info.inside,
        )

    def prepare_approval(self, path: str, old: str, new: str) -> ApprovalPreview:
        """Build the Diff preview; never writes."""
        state = self._prepare(path, old, new)
        clause = _plus_minus_clause(state.added, state.removed)
        summary = f"Edit {state.display} {clause}"
        if not state.inside:
            summary = f"{summary} (outside workspace)"
        return ApprovalPreview(
            summary=summary,
            detail=state.diff,
            mtime_ns=state.mtime_ns,
            display=core_display.diff(path=state.display, unified=state.diff),
        )

    async def forward(  # type: ignore[override]
        self,
        path: str,
        old: str,
        new: str,
    ) -> ToolOutput:
        started = time.monotonic()
        state = self._prepare(path, old, new)

        try:
            atomic_write_workspace(
                state.resolved,
                _encode_text(state.after, had_bom=state.had_bom),
                preserve_from=state.resolved,
            )
        except OSError as exc:
            raise ToolCallError(f"failed to write {state.display}: {exc}") from exc

        self._session.mark_read(state.resolved)
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        clause = _plus_minus_clause(state.added, state.removed)
        text = f"edited {state.display} {clause}"
        summary = f"Edit {state.display} · +{state.added} {_MINUS}{state.removed}"
        return ToolOutput(
            text,
            summary=summary,
            ok=True,
            duration_ms=elapsed_ms,
            display=core_display.diff(path=state.display, unified=state.diff),
        )


class WriteFileTool(Tool):
    """Create or overwrite a text file with Diff approval."""

    name = "write_file"
    description = (
        "Create a new text file or overwrite an existing one (limit 1 MB). "
        "Existing files must have been read in this session and unchanged since. "
        "Shows a unified diff for approval. Changes cannot be undone automatically."
    )
    parallel_safe = False
    timeout = None
    max_output_chars = 4_000
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "path": {
                "type": "string",
                "description": "File to write, relative to the workspace.",
            },
            "content": {
                "type": "string",
                "description": "Full file contents (UTF-8, at most 1 MB).",
            },
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace: Workspace, policy_session: PolicySession) -> None:
        self._workspace = workspace
        self._session = policy_session

    def _prepare(self, path: str, content: str) -> _WriteState:
        if not isinstance(content, str):
            raise ToolCallError("content must be a string")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            raise ToolCallError("content too large (limit 1 MB)")

        try:
            resolved_info = self._workspace.resolve_safe(
                path,
                granted_folders=self._session.granted_folders,
            )
        except WorkspaceError as exc:
            raise ToolCallError(str(exc)) from exc

        resolved = resolved_info.path
        display = resolved_info.display
        is_new = not resolved.exists()
        creates = _missing_parent_displays(resolved, self._workspace) if is_new else []

        if is_new:
            text = normalize_newlines(content, "\n")
            diff = unified_create_diff(display, text)
            added, removed = count_plus_minus(diff)
            lines = _line_count(text)
            summary = f"Create {display} (new file, {lines} lines"
            if creates:
                summary = f"{summary}; creates {', '.join(creates)}"
            summary = f"{summary})"
            if not resolved_info.inside:
                summary = f"{summary} (outside workspace)"
            return _WriteState(
                resolved=resolved,
                display=display,
                content=text,
                diff=diff,
                summary=summary,
                mtime_ns=None,
                is_new=True,
                had_bom=False,
                added=added,
                removed=removed,
                line_count=lines,
                creates=creates,
                inside=resolved_info.inside,
            )

        mtime_ns = _require_read(self._session, resolved, display)
        before, had_bom, newline = _read_text_file(resolved)
        after = normalize_newlines(content, newline)
        diff = unified_edit_diff(display, before, after)
        added, removed = count_plus_minus(diff)
        summary = f"Overwrite {display} {_plus_minus_clause(added, removed)}"
        if not resolved_info.inside:
            summary = f"{summary} (outside workspace)"
        return _WriteState(
            resolved=resolved,
            display=display,
            content=after,
            diff=diff,
            summary=summary,
            mtime_ns=mtime_ns,
            is_new=False,
            had_bom=had_bom,
            added=added,
            removed=removed,
            line_count=_line_count(after),
            creates=[],
            inside=resolved_info.inside,
        )

    def prepare_approval(self, path: str, content: str) -> ApprovalPreview:
        """Build the Diff preview; never writes or creates parents."""
        state = self._prepare(path, content)
        return ApprovalPreview(
            summary=state.summary,
            detail=state.diff,
            mtime_ns=state.mtime_ns,
            display=core_display.diff(path=state.display, unified=state.diff),
        )

    async def forward(  # type: ignore[override]
        self,
        path: str,
        content: str,
    ) -> ToolOutput:
        started = time.monotonic()
        state = self._prepare(path, content)

        try:
            state.resolved.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_workspace(
                state.resolved,
                _encode_text(state.content, had_bom=state.had_bom),
                preserve_from=None if state.is_new else state.resolved,
            )
        except OSError as exc:
            raise ToolCallError(f"failed to write {state.display}: {exc}") from exc

        self._session.mark_read(state.resolved)
        elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
        if state.is_new:
            text = f"created {state.display} ({state.line_count} lines)"
            summary = f"Write {state.display} · created, {state.line_count} lines"
        else:
            clause = _plus_minus_clause(state.added, state.removed)
            text = f"wrote {state.display} {clause}"
            summary = f"Write {state.display} · +{state.added} {_MINUS}{state.removed}"
        return ToolOutput(
            text,
            summary=summary,
            ok=True,
            duration_ms=elapsed_ms,
            display=core_display.diff(path=state.display, unified=state.diff),
        )


def build_edit_tools(
    workspace: Workspace,
    policy_session: PolicySession,
) -> list[Tool]:
    """Return `edit_file` and `write_file` bound to `workspace`."""
    return [
        EditFileTool(workspace, policy_session),
        WriteFileTool(workspace, policy_session),
    ]


__all__ = [
    "EDIT_TOOL_NAMES",
    "MAX_WRITE_BYTES",
    "EditFileTool",
    "WriteFileTool",
    "atomic_write_workspace",
    "build_edit_tools",
    "closest_lines",
    "count_plus_minus",
    "detect_newline",
    "normalize_newlines",
    "row_summary",
    "running_summary",
    "unified_create_diff",
    "unified_edit_diff",
]
