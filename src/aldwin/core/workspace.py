"""Workspace boundary, safe path resolution, and session grant management."""

from __future__ import annotations

import getpass
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Final, Protocol

from aldwin.core.home import home_dir

MAX_SYMLINK_DEPTH: Final[int] = 40


class WorkspaceError(Exception):
    """Base error raised when a workspace root or path argument is invalid."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when the requested workspace directory does not exist."""


class WorkspaceNotAllowedError(WorkspaceError):
    """Raised when the requested workspace directory is too broad or disallowed."""


class _GrantClearableSession(Protocol):
    """Structural protocol for a `PolicySession` whose grants clear on root switch."""

    grants: set[str]
    granted_folders: set[Path]
    read_files: dict[Path, int]


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """Result of resolving a path safely against a `Workspace`."""

    path: Path
    display: str
    inside: bool
    in_granted_folder: bool


def _is_case_insensitive_os() -> bool:
    """Return `True` on macOS and Windows where default filesystems fold case."""
    return sys.platform in ("darwin", "win32")


def _normalize_parts(path: Path, *, case_insensitive: bool) -> PurePath:
    """Convert `path` components into a `PurePath`, folding case when requested."""
    if case_insensitive:
        return PurePath(*(part.casefold() for part in path.parts))
    return PurePath(*path.parts)


def is_path_within(
    child: Path,
    parent: Path,
    *,
    case_insensitive: bool | None = None,
) -> bool:
    """Return `True` if `child` is equal to or inside `parent` by path components.

    Uses `PurePath.relative_to` on path components (never `str.startswith`),
    comparing case-insensitively on macOS and Windows.
    """
    fold = _is_case_insensitive_os() if case_insensitive is None else case_insensitive
    norm_child = _normalize_parts(child, case_insensitive=fold)
    norm_parent = _normalize_parts(parent, case_insensitive=fold)
    try:
        norm_child.relative_to(norm_parent)
        return True
    except ValueError:
        return False


def relative_path_str(
    child: Path,
    parent: Path,
    *,
    case_insensitive: bool | None = None,
) -> str | None:
    """Return the relative path string from `parent` to `child`, or `None`."""
    fold = _is_case_insensitive_os() if case_insensitive is None else case_insensitive
    if not is_path_within(child, parent, case_insensitive=fold):
        return None
    n_parent = len(parent.parts)
    rel_parts = child.parts[n_parent:]
    if not rel_parts:
        return "."
    return "/".join(rel_parts)


def format_outside_display(path: Path) -> str:
    """Format an absolute path using `~` shorthand when inside `Path.home`."""
    user_home = Path.home().resolve()
    rel = relative_path_str(path, user_home)
    if rel is None:
        return str(path)
    if rel == ".":
        return "~"
    return f"~/{rel}"


def _expand_user_safe(raw: str) -> str:
    """Expand `~` or `~<current_user>` without expanding environment variables."""
    if not raw.startswith("~"):
        return raw
    slash_idx = raw.find("/")
    prefix = raw if slash_idx == -1 else raw[:slash_idx]
    rest = "" if slash_idx == -1 else raw[slash_idx:]

    if prefix == "~":
        return f"{Path.home()}{rest}"

    username = prefix[1:]
    current_user = (
        os.environ.get("USER") or os.environ.get("LOGNAME") or getpass.getuser()
    )
    if username != current_user:
        raise WorkspaceError(
            f"Refused path {raw!r}: '~user' expansion is only allowed for the "
            "current user."
        )
    return f"{Path.home()}{rest}"


def _resolve_walk(base_abs: Path, *, max_symlinks: int = MAX_SYMLINK_DEPTH) -> Path:
    """Walk `base_abs` component by component, resolving symlinks and `..` safely.

    Follows symlinks even when their target does not yet exist on disk,
    resolves existing ancestors with `strict=True`, and collapses `..` after
    resolving any preceding symlink.

    Known limit: hard links are not detected; a hard link inside the
    workspace to an outside file resolves as inside, though `BLOCKED` name
    patterns still match the link's own name.
    """
    anchor = base_abs.anchor or "/"
    current = Path(anchor).resolve(strict=True)
    queue: list[str] = list(base_abs.parts[1:])
    symlinks_followed = 0

    while queue:
        part = queue.pop(0)
        if part == "..":
            if current != Path(current.anchor):
                parent = current.parent
                current = parent.resolve(strict=True) if parent.exists() else parent
            continue

        candidate = current / part
        if candidate.is_symlink():
            symlinks_followed += 1
            if symlinks_followed > max_symlinks:
                raise WorkspaceError(
                    f"Symlink loop detected while resolving {base_abs}."
                )
            raw_target = os.readlink(candidate)
            expanded_target = _expand_user_safe(raw_target)
            target_path = Path(expanded_target)
            if target_path.is_absolute():
                t_anchor = target_path.anchor or "/"
                current = Path(t_anchor).resolve(strict=True)
                queue = list(target_path.parts[1:]) + queue
            else:
                queue = list(target_path.parts) + queue
            continue

        if candidate.exists():
            current = candidate.resolve(strict=True)
        else:
            current = candidate

    tail_parts: list[str] = []
    probe = current
    while not probe.exists():
        tail_parts.append(probe.name)
        probe = probe.parent

    resolved_existing = probe.resolve(strict=True)
    for segment in reversed(tail_parts):
        resolved_existing = resolved_existing / segment

    return resolved_existing


def validate_workspace_root(
    raw_root: Path | str,
    *,
    home: Path | None = None,
) -> Path:
    """Validate and resolve a workspace root directory."""
    raw_str = str(raw_root).strip()
    if not raw_str or "\x00" in raw_str:
        raise WorkspaceNotFoundError(f"workspace not found: {raw_root}")

    try:
        expanded_str = _expand_user_safe(raw_str)
    except WorkspaceError as exc:
        raise WorkspaceNotFoundError(f"workspace not found: {raw_str}") from exc

    candidate = Path(expanded_str)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceNotFoundError(f"workspace not found: {raw_str}") from exc

    if not resolved.is_dir():
        raise WorkspaceNotFoundError(f"workspace not found: {raw_str}")

    fs_root = Path(resolved.anchor or "/").resolve(strict=True)
    user_home = Path.home().resolve(strict=True)
    fold = _is_case_insensitive_os()

    if _normalize_parts(resolved, case_insensitive=fold) == _normalize_parts(
        fs_root, case_insensitive=fold
    ):
        raise WorkspaceNotAllowedError("workspace too broad: /")

    if _normalize_parts(resolved, case_insensitive=fold) == _normalize_parts(
        user_home, case_insensitive=fold
    ):
        shown = "~" if raw_str.startswith("~") else format_outside_display(resolved)
        raise WorkspaceNotAllowedError(f"workspace too broad: {shown}")

    aldwin_root = (home if home is not None else home_dir()).expanduser().resolve()
    if is_path_within(resolved, aldwin_root, case_insensitive=fold):
        shown = format_outside_display(resolved)
        raise WorkspaceNotAllowedError(f"workspace not allowed: {shown}")

    return resolved


DEFAULT_WORKSPACE_DIRNAME: Final[str] = "aldwin_workspace"


def default_workspace_root(home: Path | None = None) -> Path:
    """Return the workspace root for a launch without an explicit `--workspace`.

    The current directory is used when it is an allowed root. When it is not
    (`/`, the user's home, or a folder inside the Aldwin home), or when it no
    longer exists, a sibling of the Aldwin home named `aldwin_workspace` is
    created and used instead, so typing `aldwin` from the home directory still
    starts a session.
    """
    try:
        return validate_workspace_root(Path.cwd(), home=home)
    except (WorkspaceError, OSError):
        # OSError: the shell's current directory was deleted after `cd`.
        pass

    aldwin_root = (home if home is not None else home_dir()).expanduser().resolve()
    fallback = aldwin_root.parent / DEFAULT_WORKSPACE_DIRNAME
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        shown = format_outside_display(fallback)
        raise WorkspaceNotFoundError(f"workspace not found: {shown}") from exc
    return validate_workspace_root(fallback, home=home)


class Workspace:
    """Enforces the workspace root boundary and resolves paths safely."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        home: Path | None = None,
    ) -> None:
        self._home = home
        target_root: Path | str = root if root is not None else Path.cwd()
        self._root: Path = validate_workspace_root(target_root, home=home)
        self._read_files: dict[Path, int] = {}

    @property
    def root(self) -> Path:
        """Return the resolved absolute workspace root directory."""
        return self._root

    @property
    def display_root(self) -> str:
        """Return the human-friendly workspace root path (`~` shorthand)."""
        return format_outside_display(self._root)

    @property
    def read_files(self) -> dict[Path, int]:
        """Return the map of resolved file paths read in this workspace."""
        return self._read_files

    def resolve_safe(
        self,
        path: str | Path,
        *,
        granted_folders: Iterable[Path] = (),
        case_insensitive: bool | None = None,
    ) -> ResolvedPath:
        """Resolve `path` safely relative to `self.root`.

        Expands `~` for the current user only, rejects `$VAR` expansion and null
        bytes, resolves symlinks and `..` component by component, and checks
        membership in `self.root` and `granted_folders` using path components.
        """
        if not isinstance(path, (str, Path)):
            raise WorkspaceError(f"Expected a path string, got {type(path).__name__}.")
        raw = str(path)
        if not raw or not raw.strip() or "\x00" in raw:
            raise WorkspaceError("Path must be a non-empty string without null bytes.")
        if "$" in raw:
            raise WorkspaceError(
                f"Refused path {raw!r}: environment variable expansion is not allowed."
            )

        expanded = _expand_user_safe(raw.strip())
        candidate = Path(expanded)
        if not candidate.is_absolute():
            candidate = self._root / candidate

        resolved = _resolve_walk(candidate)
        fold = (
            _is_case_insensitive_os() if case_insensitive is None else case_insensitive
        )

        inside = is_path_within(resolved, self._root, case_insensitive=fold)
        in_granted = any(
            is_path_within(resolved, folder, case_insensitive=fold)
            for folder in granted_folders
        )

        if inside:
            display = relative_path_str(
                resolved, self._root, case_insensitive=fold
            ) or str(resolved)
        else:
            display = format_outside_display(resolved)

        return ResolvedPath(
            path=resolved,
            display=display,
            inside=inside,
            in_granted_folder=in_granted,
        )

    def mark_read(
        self,
        path: str | Path,
        *,
        session: _GrantClearableSession | None = None,
    ) -> Path:
        """Record `path` and its current `st_mtime_ns` in `read_files`."""
        resolved = (
            path
            if isinstance(path, Path) and path.is_absolute()
            else self.resolve_safe(path).path
        )
        mtime_ns = resolved.stat().st_mtime_ns if resolved.exists() else 0
        self._read_files[resolved] = mtime_ns
        if session is not None:
            session.read_files[resolved] = mtime_ns
        return resolved

    def set_root(
        self,
        new_root: Path | str,
        *,
        session: _GrantClearableSession | None = None,
    ) -> Path:
        """Switch the workspace root and clear all session grants."""
        validated = validate_workspace_root(new_root, home=self._home)
        self._root = validated
        self._read_files.clear()
        if session is not None:
            session.grants.clear()
            session.granted_folders.clear()
        return self._root


__all__ = [
    "DEFAULT_WORKSPACE_DIRNAME",
    "MAX_SYMLINK_DEPTH",
    "ResolvedPath",
    "Workspace",
    "WorkspaceError",
    "WorkspaceNotAllowedError",
    "WorkspaceNotFoundError",
    "default_workspace_root",
    "format_outside_display",
    "is_path_within",
    "relative_path_str",
    "validate_workspace_root",
]
