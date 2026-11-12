"""Read-only workspace tools: `list_dir`, `glob`, `grep`, and `read_file`."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
from asyncio.subprocess import Process
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final

import pathspec
from agentling import Tool

from aldwin.core.policy import BLOCKED_PATTERNS, PolicySession, is_blocked_path
from aldwin.core.tools import ToolOutput
from aldwin.core.tools import display as core_display
from aldwin.core.workspace import (
    Workspace,
    WorkspaceError,
    format_outside_display,
    is_path_within,
    relative_path_str,
)

READ_TIMEOUT_SECONDS: Final[float] = 10.0
LIST_TIMEOUT_SECONDS: Final[float] = 10.0
GLOB_TIMEOUT_SECONDS: Final[float] = 15.0
GREP_TIMEOUT_SECONDS: Final[float] = 30.0

MAX_READ_BYTES: Final[int] = 5 * 1024 * 1024
BINARY_PROBE_BYTES: Final[int] = 8192
MAX_PAGE_LINES: Final[int] = 2000
MAX_LINE_CHARS: Final[int] = 2000
LIST_CAP: Final[int] = 300
GLOB_CAP: Final[int] = 500
GREP_CAP: Final[int] = 200
GREP_LINE_CHARS: Final[int] = 200
MAX_LIST_DEPTH: Final[int] = 3
MIN_LIST_DEPTH: Final[int] = 1
SUGGEST_MAX_DISTANCE: Final[int] = 2

FILE_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"list_dir", "glob", "grep", "read_file"}
)

ALWAYS_SKIP_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
    }
)
ALWAYS_SKIP_FILES: Final[frozenset[str]] = frozenset({".DS_Store"})

_OBJECT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
}


def format_size(num_bytes: int) -> str:
    """Format `num_bytes` as a binary size (`1024` → `1.0 KB`)."""
    size = max(0, num_bytes)
    if size < 1024:
        return f"{size} B"
    value = float(size)
    unit = "KB"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024.0 or unit == "TB":
            break
    return f"{value:.1f} {unit}"


def running_summary(
    name: str,
    arguments: dict[str, Any] | None,
    workspace: Workspace,
) -> str:
    """One-line pending row for a file tool, or `""` for any other tool."""
    if name not in FILE_TOOL_NAMES:
        return ""
    args = arguments or {}
    if name == "glob":
        return f"Find {args.get('pattern', '')}"
    if name == "grep":
        pattern = args.get("pattern", "")
        head = f'Search "{pattern}"'
        glob = args.get("glob")
        if isinstance(glob, str) and glob.strip():
            return f"{head} in {glob}"
        raw_path = args.get("path")
        if isinstance(raw_path, str) and raw_path not in ("", "."):
            return f"{head} in {_display_arg(workspace, raw_path)}"
        return head
    if name == "list_dir":
        raw_path = args.get("path", ".")
        shown = raw_path if isinstance(raw_path, str) else "."
        return f"List {_display_arg(workspace, shown or '.')}"
    raw_path = args.get("path", "")
    shown = raw_path if isinstance(raw_path, str) else str(raw_path)
    return f"Read {_display_arg(workspace, shown)}"


def row_summary(running: str, summary: str, *, ok: bool) -> str:
    """Rewrite a denial row. Successful tool summaries stay as the tool wrote them."""
    if ok or not running:
        return summary
    lower = summary.lower()
    if summary == "declined" or "denied by the user" in lower:
        return f"{running} · declined"
    if summary == "not run: no approver" or "no one was available to approve" in lower:
        return f"{running} · not run: no one to approve"
    if summary == "blocked: pattern" or "search pattern must stay inside" in lower:
        return f"{running} · blocked: pattern must stay inside the search folder"
    if (summary.startswith("blocked:") and "secret" in lower) or (
        "blocked because it may hold secrets" in lower
    ):
        return f"{running} · blocked: may contain secrets"
    return summary


async def checkpoint(visited: int) -> None:
    """Yield during a long walk so cancellation can land."""
    if visited > 0 and visited % 50 == 0:
        await asyncio.sleep(0)


class _RgFallback(Exception):
    """`rg` was missing or failed; the Python search should run instead."""


@dataclass
class _IgnoreRules:
    """Always-skip directories plus `.gitignore` rules from the search root down."""

    root: Path
    include_ignored: bool = False
    _loaded: set[Path] = field(default_factory=set)
    _specs: list[tuple[Path, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.load(self.root)

    def load_ancestors(self, workspace_root: Path) -> None:
        """Load ignore files from the workspace root down through this search root."""
        if not is_path_within(self.root, workspace_root):
            return
        current = workspace_root
        self.load(current)
        try:
            relative = self.root.relative_to(workspace_root)
        except ValueError:
            return
        for part in relative.parts:
            current = current / part
            self.load(current)

    def load(self, directory: Path) -> None:
        """Load `directory/.gitignore` once."""
        if directory in self._loaded:
            return
        self._loaded.add(directory)
        gitignore = directory / ".gitignore"
        try:
            text = gitignore.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        spec = pathspec.GitIgnoreSpec.from_lines("gitignore", text.splitlines())
        self._specs.append((directory, spec))

    def prepare(self, path: Path) -> None:
        """Load ignore files for every directory from the root down to `path`."""
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return
        current = self.root
        self.load(current)
        if path.is_dir():
            parts = relative.parts
        else:
            parts = relative.parts[:-1]
        for part in parts:
            current = current / part
            self.load(current)

    def always_skip(self, path: Path, *, is_dir: bool) -> bool:
        """Return whether this entry's own name is always skipped."""
        if path.name in ALWAYS_SKIP_FILES:
            return True
        return is_dir and path.name in ALWAYS_SKIP_DIRS

    def gitignored(self, path: Path, *, is_dir: bool) -> bool:
        """Return whether a `.gitignore` rule hides `path`."""
        if self.include_ignored:
            return False
        self.prepare(path)
        for spec_dir, spec in self._specs:
            try:
                relative = path.relative_to(spec_dir).as_posix()
            except ValueError:
                continue
            if relative in ("", "."):
                continue
            if spec.match_file(relative):
                return True
            if is_dir and spec.match_file(f"{relative}/"):
                return True
        return False


def _display_arg(workspace: Workspace, raw_path: str) -> str:
    try:
        return workspace.resolve_safe(raw_path).display
    except (WorkspaceError, OSError, ValueError):
        return raw_path


def _noun(count: int, singular: str, plural: str) -> str:
    word = singular if count == 1 else plural
    return f"{count} {word}"


def _hidden_footer(hidden: int) -> str:
    if hidden <= 0:
        return ""
    return f"({hidden} entries hidden: may hold secrets)"


def _with_hidden(summary: str, hidden: int) -> str:
    if hidden <= 0:
        return summary
    return f"{summary} · {hidden} hidden: may hold secrets"


def _fail(message: str) -> ToolOutput:
    return ToolOutput(message, summary=message, ok=False)


def _timeout_result(seconds: int) -> ToolOutput:
    message = f"timed out after {seconds}s"
    return ToolOutput(message, summary=message, ok=False)


def _levenshtein(left: str, right: str, *, max_dist: int) -> int | None:
    if abs(len(left) - len(right)) > max_dist:
        return None
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        row_min = index
        for right_index, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            best = min(
                current[right_index - 1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + cost,
            )
            current.append(best)
            if best < row_min:
                row_min = best
        if row_min > max_dist:
            return None
        previous = current
    distance = previous[-1]
    if distance > max_dist:
        return None
    return distance


def _suggestion(user_path: str, parent: Path) -> str | None:
    missing = Path(user_path).name
    try:
        names = sorted(entry.name for entry in parent.iterdir())
    except OSError:
        return None
    best_name: str | None = None
    best_distance = SUGGEST_MAX_DISTANCE + 1
    for name in names:
        if name == missing:
            continue
        distance = _levenshtein(missing, name, max_dist=SUGGEST_MAX_DISTANCE)
        if distance is not None and distance < best_distance:
            best_distance = distance
            best_name = name
    if best_name is None:
        return None
    parent_text = str(PurePosixPath(user_path).parent)
    if parent_text in ("", "."):
        return best_name
    return f"{parent_text}/{best_name}"


def _not_found(user_path: str, parent: Path | None) -> ToolOutput:
    if parent is not None and parent.is_dir():
        suggestion = _suggestion(user_path, parent)
        if suggestion is not None:
            return _fail(f"not found: {user_path} (did you mean {suggestion}?)")
    return _fail(f"not found: {user_path}")


def _blocked_refusal(path: Path, workspace: Workspace) -> ToolOutput:
    shown = _display_path(path, workspace)
    return _fail(
        f"Not run: {shown} is blocked because it may hold secrets. "
        "This can't be approved."
    )


def _blocked(path: Path, *, tool_name: str, home: Path) -> bool:
    try:
        raw = path.as_posix()
    except ValueError:
        raw = str(path)
    return is_blocked_path(path, raw_path=raw, tool_name=tool_name, aldwin_home=home)


def _display_path(path: Path, workspace: Workspace) -> str:
    relative = relative_path_str(path, workspace.root)
    if relative is not None:
        return relative
    return format_outside_display(path)


def _glob_match(relative: str, pattern: str) -> bool:
    candidate = PurePosixPath(relative)
    if candidate.match(pattern):
        return True
    return PurePosixPath(candidate.name).match(pattern)


def _cut(text: str, limit: int, *, ellipsis: bool) -> str:
    if len(text) <= limit:
        return text
    if ellipsis:
        return f"{text[:limit]}…"
    return text[:limit]


async def _terminate_process_group(
    proc: Process, *, grace_seconds: float = 0.2
) -> None:
    """Stop `proc` and its process group."""
    if proc.returncode is not None:
        return
    pid = proc.pid
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        return
    except TimeoutError:
        pass
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=1.0)
    except TimeoutError:
        pass


class _FileTool(Tool):
    """Shared workspace and policy session for the four read-only tools."""

    timeout = None
    parallel_safe = True

    def __init__(self, workspace: Workspace, policy_session: PolicySession) -> None:
        self._workspace = workspace
        self._session = policy_session

    def _resolve(self, raw_path: str) -> Path | ToolOutput:
        try:
            resolved = self._workspace.resolve_safe(
                raw_path,
                granted_folders=self._session.granted_folders,
            )
        except WorkspaceError as exc:
            return _fail(str(exc))
        return resolved.path


class ReadFileTool(_FileTool):
    """Read a text file as numbered lines."""

    name = "read_file"
    description = (
        "Read a text file in the workspace and return numbered lines. "
        "Use this to look at a file. Refuses directories, binary files, "
        "and files larger than 5 MB."
    )
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "path": {
                "type": "string",
                "description": "File to read, relative to the workspace.",
            },
            "offset": {
                "type": "integer",
                "description": "First line to return. Lines are numbered from 1.",
                "default": 1,
            },
            "limit": {
                "type": "integer",
                "description": "How many lines to return, capped at 2000.",
                "default": MAX_PAGE_LINES,
            },
        },
        "required": ["path"],
    }

    async def forward(  # type: ignore[override]
        self,
        path: str,
        offset: int = 1,
        limit: int = MAX_PAGE_LINES,
    ) -> ToolOutput:
        try:
            async with asyncio.timeout(READ_TIMEOUT_SECONDS):
                return await self._read(path, offset, limit)
        except TimeoutError:
            return _timeout_result(10)

    async def _read(self, path: str, offset: int, limit: int) -> ToolOutput:
        resolved = self._resolve(path)
        if isinstance(resolved, ToolOutput):
            return resolved
        if _blocked(resolved, tool_name=self.name, home=self._session.home):
            return _blocked_refusal(resolved, self._workspace)
        if resolved.is_dir():
            return _fail(f"is a directory: {path}; use list_dir")
        if not resolved.is_file():
            parent = resolved.parent if resolved.parent != resolved else None
            return _not_found(path, parent)
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            return _fail(str(exc))
        if size > MAX_READ_BYTES:
            shown = format_size(size)
            return _fail(
                f"too large: {shown} (limit 5 MB); use grep to find the part you need"
            )
        try:
            with resolved.open("rb") as handle:
                probe = handle.read(BINARY_PROBE_BYTES)
        except OSError as exc:
            return _fail(str(exc))
        if b"\x00" in probe:
            return _fail("binary file; use read_document for PDFs and Office files")

        start = 1 if offset < 1 else offset
        page_limit = 1 if limit < 1 else min(limit, MAX_PAGE_LINES)
        page, total = await asyncio.to_thread(_read_page, resolved, start, page_limit)
        if total == 0 and start <= 1:
            self._session.mark_read(resolved)
            summary = (
                f"Read {_display_path(resolved, self._workspace)} · lines 0–0 of 0"
            )
            return ToolOutput("", summary=summary, ok=True)
        if start > total:
            return _fail(f"offset {start} is past the end ({total} lines)")

        last = start + len(page) - 1
        lines = [
            f"{number}\t{_cut(text, MAX_LINE_CHARS, ellipsis=True)}"
            for number, text in enumerate(page, start=start)
        ]
        if total > last:
            next_offset = last + 1
            lines.append(
                f"… lines {next_offset:,}–{total:,} not shown; "
                f"call again with offset={next_offset}"
            )
        display = _display_path(resolved, self._workspace)
        summary = f"Read {display} · lines {start}–{last} of {total}"
        self._session.mark_read(resolved)
        return ToolOutput("\n".join(lines), summary=summary, ok=True)


def _read_page(path: Path, start: int, limit: int) -> tuple[list[str], int]:
    page: list[str] = []
    total = 0
    end = start + limit - 1
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for raw_line in handle:
            total += 1
            if start <= total <= end:
                page.append(raw_line.rstrip("\r\n"))
    return page, total


class ListDirTool(_FileTool):
    """List a directory, skipping ignored and always-skip names."""

    name = "list_dir"
    description = (
        "List files and folders under a workspace directory. "
        "Directories come first. Hidden and ignored names are omitted."
    )
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list. Defaults to the workspace root.",
                "default": ".",
            },
            "depth": {
                "type": "integer",
                "description": "How many levels to list, from 1 to 3.",
                "default": 1,
            },
            "include_ignored": {
                "type": "boolean",
                "description": "Also list files that .gitignore would hide.",
                "default": False,
            },
        },
        "required": [],
    }

    async def forward(  # type: ignore[override]
        self,
        path: str = ".",
        depth: int = 1,
        include_ignored: bool = False,
    ) -> ToolOutput:
        try:
            async with asyncio.timeout(LIST_TIMEOUT_SECONDS):
                return await self._walk(path, depth, include_ignored)
        except TimeoutError:
            return _timeout_result(10)

    async def _walk(self, path: str, depth: int, include_ignored: bool) -> ToolOutput:
        resolved = self._resolve(path)
        if isinstance(resolved, ToolOutput):
            return resolved
        if not resolved.exists():
            return _not_found(path, resolved.parent)
        if not resolved.is_dir():
            return _fail(f"not a directory: {path}; use read_file")

        notice = ""
        used = depth
        if depth > MAX_LIST_DEPTH:
            used = MAX_LIST_DEPTH
            notice = "depth clamped to 3"
        elif depth < MIN_LIST_DEPTH:
            used = MIN_LIST_DEPTH
            notice = "depth clamped to 1"

        rules = _IgnoreRules(resolved, include_ignored=include_ignored)
        rules.load_ancestors(self._workspace.root)
        lines: list[str] = []
        file_entries: list[dict[str, str]] = []
        hidden = 0
        unreadable = 0
        visited = 0

        async def walk(directory: Path, relative: str, levels_left: int) -> None:
            nonlocal hidden, unreadable, visited
            try:
                entries = list(os.scandir(directory))
            except OSError:
                unreadable += 1
                return
            pending: list[tuple[int, str, Path, bool]] = []
            for entry in entries:
                visited += 1
                await checkpoint(visited)
                child = Path(entry.path)
                try:
                    is_link = entry.is_symlink()
                except OSError:
                    unreadable += 1
                    continue
                is_dir = False
                if not is_link:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        unreadable += 1
                        continue
                skip_dir = is_dir or child.name in ALWAYS_SKIP_DIRS
                if rules.always_skip(child, is_dir=skip_dir):
                    continue
                if _blocked(child, tool_name=self.name, home=self._session.home):
                    hidden += 1
                    continue
                if rules.gitignored(child, is_dir=is_dir):
                    continue
                rel = f"{relative}/{entry.name}" if relative else entry.name
                if is_dir:
                    if not os.access(child, os.R_OK | os.X_OK):
                        unreadable += 1
                        continue
                    pending.append((0, rel, child, True))
                else:
                    pending.append((1, rel, child, False))
            pending.sort(key=lambda item: (item[0], item[1]))
            for _order, rel, child, is_dir in pending:
                if is_dir:
                    lines.append(f"{rel}/")
                    file_entries.append({"path": rel, "kind": "folder"})
                    if levels_left > 1:
                        rules.load(child)
                        await walk(child, rel, levels_left - 1)
                    continue
                try:
                    stat = child.lstat()
                except OSError:
                    unreadable += 1
                    continue
                when = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
                lines.append(f"{rel}  {format_size(stat.st_size)}  {when}")
                file_entries.append({"path": rel, "kind": "file"})

        await walk(resolved, "", used)
        shown = lines[:LIST_CAP]
        extra = len(lines) - len(shown)
        body: list[str] = []
        if notice:
            body.append(notice)
        body.extend(shown)
        if extra > 0:
            body.append(f"… and {extra} more")
        if unreadable > 0:
            body.append(f"({unreadable} unreadable)")
        footer = _hidden_footer(hidden)
        if footer:
            body.append(footer)
        display = _display_path(resolved, self._workspace)
        summary = _with_hidden(
            f"List {display} · {_noun(len(shown), 'item', 'items')}",
            hidden,
        )
        return ToolOutput(
            "\n".join(body),
            summary=summary,
            ok=True,
            display=core_display.files(root=display, entries=file_entries),
        )


class GlobTool(_FileTool):
    """Find files by a glob, newest first."""

    name = "glob"
    description = (
        "Find files by a glob pattern under a workspace directory, newest first. "
        "The pattern stays inside that directory."
    )
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob to match, such as **/*.py.",
            },
            "path": {
                "type": "string",
                "description": "Directory to search. Defaults to the workspace root.",
                "default": ".",
            },
            "include_ignored": {
                "type": "boolean",
                "description": "Also return files that .gitignore would hide.",
                "default": False,
            },
        },
        "required": ["pattern"],
    }

    async def forward(  # type: ignore[override]
        self,
        pattern: str,
        path: str = ".",
        include_ignored: bool = False,
    ) -> ToolOutput:
        try:
            async with asyncio.timeout(GLOB_TIMEOUT_SECONDS):
                return await self._walk(pattern, path, include_ignored)
        except TimeoutError:
            return _timeout_result(15)

    async def _walk(
        self,
        pattern: str,
        path: str,
        include_ignored: bool,
    ) -> ToolOutput:
        if pattern.strip() == "":
            message = f"no files match {pattern}"
            return ToolOutput(message, summary=message, ok=True)
        resolved = self._resolve(path)
        if isinstance(resolved, ToolOutput):
            return resolved
        if not resolved.exists():
            return _not_found(path, resolved.parent)
        if not resolved.is_dir():
            return _fail(f"not a directory: {path}; use read_file")

        rules = _IgnoreRules(resolved, include_ignored=include_ignored)
        rules.load_ancestors(self._workspace.root)
        found: list[tuple[int, str]] = []
        hidden = 0
        visited = 0
        for candidate in resolved.glob(pattern):
            visited += 1
            await checkpoint(visited)
            if not _keep_glob_path(candidate, resolved):
                continue
            if rules.always_skip(candidate, is_dir=candidate.is_dir()):
                continue
            is_dir = candidate.is_dir() and not candidate.is_symlink()
            if _blocked(candidate, tool_name=self.name, home=self._session.home):
                hidden += 1
                continue
            if rules.gitignored(candidate, is_dir=is_dir):
                continue
            if is_dir:
                continue
            if not candidate.is_file() and not candidate.is_symlink():
                continue
            try:
                modified = candidate.lstat().st_mtime_ns
            except OSError:
                continue
            found.append((modified, _display_path(candidate, self._workspace)))

        found.sort(key=lambda item: (-item[0], item[1]))
        paths = [item[1] for item in found]
        shown = paths[:GLOB_CAP]
        extra = len(paths) - len(shown)
        root_display = _display_path(resolved, self._workspace)
        glob_entries = [{"path": item, "kind": "file"} for item in paths]
        if not shown and hidden == 0:
            message = f"no files match {pattern}"
            return ToolOutput(
                message,
                summary=message,
                ok=True,
                display=core_display.files(root=root_display, entries=[]),
            )
        body = list(shown)
        if extra > 0:
            body.append(f"… and {extra} more")
        footer = _hidden_footer(hidden)
        if footer:
            body.append(footer)
        summary = _with_hidden(
            f"Find {pattern} · {_noun(len(shown), 'file', 'files')}",
            hidden,
        )
        return ToolOutput(
            "\n".join(body),
            summary=summary,
            ok=True,
            display=core_display.files(root=root_display, entries=glob_entries),
        )


def _keep_glob_path(candidate: Path, root: Path) -> bool:
    try:
        if candidate.is_symlink():
            target = candidate.resolve()
            if not is_path_within(target, root):
                return False
    except OSError:
        return False
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False
    return not any(part in ALWAYS_SKIP_DIRS for part in relative.parts)


@dataclass
class _GrepHit:
    display: str
    line: int
    text: str


class GrepTool(_FileTool):
    """Search file contents with `rg` when it is installed, otherwise Python."""

    name = "grep"
    description = (
        "Search file contents under a workspace directory. "
        "Prints matching lines as path:line: text. "
        "Use this instead of a shell grep."
    )
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression to search for.",
            },
            "path": {
                "type": "string",
                "description": "Folder or file to search. Defaults to the workspace.",
                "default": ".",
            },
            "glob": {
                "type": "string",
                "description": "Optional glob that limits which files are searched.",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Match letters regardless of case.",
                "default": False,
            },
            "include_ignored": {
                "type": "boolean",
                "description": "Also search files that .gitignore would hide.",
                "default": False,
            },
        },
        "required": ["pattern"],
    }

    async def forward(  # type: ignore[override]
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        ignore_case: bool = False,
        include_ignored: bool = False,
    ) -> ToolOutput:
        try:
            async with asyncio.timeout(GREP_TIMEOUT_SECONDS):
                return await self._walk(
                    pattern,
                    path,
                    glob,
                    ignore_case,
                    include_ignored,
                )
        except TimeoutError:
            return _timeout_result(30)

    async def _walk(
        self,
        pattern: str,
        path: str,
        glob: str | None,
        ignore_case: bool,
        include_ignored: bool,
    ) -> ToolOutput:
        if pattern == "":
            return _fail("invalid regex: pattern is empty")
        flags = re.IGNORECASE if ignore_case else 0
        try:
            compiled = re.compile(pattern, flags)
        except re.error as exc:
            return _fail(f"invalid regex: {exc}")

        resolved = self._resolve(path)
        if isinstance(resolved, ToolOutput):
            return resolved
        if not resolved.exists():
            return _not_found(path, resolved.parent)
        if _blocked(resolved, tool_name=self.name, home=self._session.home):
            return _blocked_refusal(resolved, self._workspace)

        rules = _IgnoreRules(
            resolved if resolved.is_dir() else resolved.parent,
            include_ignored=include_ignored,
        )
        rules.load_ancestors(self._workspace.root)
        file_glob = glob if isinstance(glob, str) and glob.strip() else None
        rg_bin = shutil.which("rg")
        hits: list[_GrepHit] | None = None
        hidden_ids: set[str] = set()
        if rg_bin is not None and resolved.is_dir():
            try:
                hits, leaked = await self._run_rg(
                    rg_bin,
                    resolved,
                    pattern,
                    file_glob,
                    ignore_case,
                    include_ignored,
                    rules,
                )
                hidden_ids.update(leaked)
            except _RgFallback:
                hits = None
        if hits is None:
            hits, hidden_ids = await self._python_grep(
                resolved,
                compiled,
                rules,
                file_glob,
            )
        else:
            extra = await self._count_blocked(
                resolved,
                compiled,
                rules,
                file_glob,
            )
            hidden_ids.update(extra)

        hits.sort(key=lambda hit: (hit.display, hit.line))
        shown = hits[:GREP_CAP]
        rest = hits[GREP_CAP:]
        body = [f"{hit.display}:{hit.line}: {hit.text}" for hit in shown]
        if rest:
            files = len({hit.display for hit in rest})
            body.append(f"… {len(rest)} more matches in {files} files")
        footer = _hidden_footer(len(hidden_ids))
        if footer:
            body.append(footer)
        if not body:
            body.append("no matches")
        summary = self._summary(
            pattern,
            file_glob,
            path,
            len(shown),
            len({hit.display for hit in shown}),
            len(hidden_ids),
        )
        return ToolOutput("\n".join(body), summary=summary, ok=True)

    def _summary(
        self,
        pattern: str,
        file_glob: str | None,
        path: str,
        matches: int,
        files: int,
        hidden: int,
    ) -> str:
        head = f'Search "{pattern}"'
        if file_glob:
            head = f"{head} in {file_glob}"
        elif path not in ("", "."):
            display = path
            resolved = self._resolve(path)
            if not isinstance(resolved, ToolOutput):
                display = _display_path(resolved, self._workspace)
            head = f"{head} in {display}"
        summary = (
            f"{head} · {_noun(matches, 'match', 'matches')} in "
            f"{_noun(files, 'file', 'files')}"
        )
        return _with_hidden(summary, hidden)

    async def _run_rg(
        self,
        rg_bin: str,
        root: Path,
        pattern: str,
        file_glob: str | None,
        ignore_case: bool,
        include_ignored: bool,
        rules: _IgnoreRules,
    ) -> tuple[list[_GrepHit], set[str]]:
        argv = [rg_bin, "--json"]
        if ignore_case:
            argv.append("-i")
        if include_ignored:
            argv.append("--no-ignore")
        for name in sorted(ALWAYS_SKIP_DIRS):
            argv.extend(["--glob", f"!{name}/**"])
        argv.extend(["--glob", "!.DS_Store"])
        for blocked in BLOCKED_PATTERNS:
            argv.extend(["--glob", f"!{blocked}"])
        if file_glob:
            argv.extend(["--glob", file_glob])
        argv.extend(["-e", pattern, "--", str(root)])
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(root),
                start_new_session=True,
            )
        except OSError as exc:
            raise _RgFallback from exc
        try:
            stdout, _stderr = await proc.communicate()
        except asyncio.CancelledError:
            await asyncio.shield(_terminate_process_group(proc))
            raise
        code = proc.returncode if proc.returncode is not None else 2
        if code not in (0, 1):
            raise _RgFallback
        return self._parse_rg(
            stdout.decode("utf-8", errors="replace"),
            root,
            rules,
        )

    def _parse_rg(
        self,
        payload: str,
        root: Path,
        rules: _IgnoreRules,
    ) -> tuple[list[_GrepHit], set[str]]:
        hits: list[_GrepHit] = []
        hidden: set[str] = set()
        for raw_line in payload.splitlines():
            if not raw_line.strip():
                continue
            try:
                item = _json_loads(raw_line)
            except ValueError:
                continue
            if not isinstance(item, dict) or item.get("type") != "match":
                continue
            data = item.get("data")
            if not isinstance(data, dict):
                continue
            raw_path = _rg_text(data.get("path"))
            line_text = _rg_text(data.get("lines")).rstrip("\r\n")
            number = data.get("line_number")
            if not isinstance(raw_path, str) or not isinstance(number, int):
                continue
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = root / candidate
            if not _path_in_root(candidate, root):
                continue
            identity = str(candidate)
            if _blocked(candidate, tool_name=self.name, home=self._session.home):
                hidden.add(identity)
                continue
            if any(part in ALWAYS_SKIP_DIRS for part in candidate.parts):
                continue
            if rules.gitignored(candidate, is_dir=False):
                continue
            hits.append(
                _GrepHit(
                    display=_display_path(candidate, self._workspace),
                    line=number,
                    text=_cut(line_text, GREP_LINE_CHARS, ellipsis=False),
                )
            )
        return hits, hidden

    async def _python_grep(
        self,
        root: Path,
        compiled: re.Pattern[str],
        rules: _IgnoreRules,
        file_glob: str | None,
    ) -> tuple[list[_GrepHit], set[str]]:
        hits: list[_GrepHit] = []
        hidden: set[str] = set()
        visited = 0
        files = [root] if root.is_file() else _walk_files(root, rules)
        for candidate in files:
            visited += 1
            await checkpoint(visited)
            relative = _relative_or_name(
                candidate, root if root.is_dir() else root.parent
            )
            if file_glob and not _glob_match(relative, file_glob):
                continue
            if rules.always_skip(candidate, is_dir=False):
                continue
            if candidate.is_symlink():
                continue
            secret_file = _blocked(
                candidate,
                tool_name=self.name,
                home=self._session.home,
            )
            if not secret_file and rules.gitignored(candidate, is_dir=False):
                continue
            matched, binary = await _search_file(candidate, compiled)
            if binary:
                continue
            if secret_file:
                if matched:
                    hidden.add(str(candidate))
                continue
            display = _display_path(candidate, self._workspace)
            for number, text in matched:
                hits.append(
                    _GrepHit(
                        display=display,
                        line=number,
                        text=_cut(text, GREP_LINE_CHARS, ellipsis=False),
                    )
                )
        return hits, hidden

    async def _count_blocked(
        self,
        root: Path,
        compiled: re.Pattern[str],
        rules: _IgnoreRules,
        file_glob: str | None,
    ) -> set[str]:
        hidden: set[str] = set()
        visited = 0
        for candidate in _walk_files(root, rules, descend_ignored=True):
            visited += 1
            await checkpoint(visited)
            if not _blocked(candidate, tool_name=self.name, home=self._session.home):
                continue
            relative = _relative_or_name(candidate, root)
            if file_glob and not _glob_match(relative, file_glob):
                continue
            matched, _secret = await _search_file(candidate, compiled)
            if matched:
                hidden.add(str(candidate))
        return hidden


def _json_loads(raw: str) -> Any:
    return json.loads(raw)


def _rg_text(value: Any) -> str:
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            return text
    if isinstance(value, str):
        return value
    return ""


def _path_in_root(candidate: Path, root: Path) -> bool:
    if is_path_within(candidate, root):
        return True
    try:
        return is_path_within(candidate.resolve(), root)
    except OSError:
        return False


def _relative_or_name(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _walk_files(
    root: Path,
    rules: _IgnoreRules,
    *,
    descend_ignored: bool = False,
) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        rules.load(current)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if child.is_symlink() or name in ALWAYS_SKIP_DIRS:
                continue
            if not descend_ignored and rules.gitignored(child, is_dir=True):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            yield current / name


async def _search_file(
    path: Path,
    compiled: re.Pattern[str],
) -> tuple[list[tuple[int, str]], bool]:
    """Return `(matches, is_binary)`. Yields every 50 lines so cancel can land."""
    try:
        with path.open("rb") as handle:
            probe = handle.read(BINARY_PROBE_BYTES)
    except OSError:
        return [], False
    if b"\x00" in probe:
        return [], True
    matches: list[tuple[int, str]] = []
    try:
        with path.open(
            "r",
            encoding="utf-8",
            errors="replace",
            newline="",
        ) as handle:
            for number, raw_line in enumerate(handle, start=1):
                if number % 50 == 0:
                    await checkpoint(number)
                text = raw_line.rstrip("\r\n")
                if compiled.search(text):
                    matches.append((number, text))
    except OSError:
        return [], False
    return matches, False


def build_file_tools(
    workspace: Workspace,
    policy_session: PolicySession,
) -> list[Tool]:
    """Return the four read-only file tools bound to `workspace`."""
    return [
        ListDirTool(workspace, policy_session),
        GlobTool(workspace, policy_session),
        GrepTool(workspace, policy_session),
        ReadFileTool(workspace, policy_session),
    ]
