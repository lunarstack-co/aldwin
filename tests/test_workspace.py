"""Unit and Hypothesis property tests for `aldwin.core.workspace`."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aldwin.core.workspace import (
    DEFAULT_WORKSPACE_DIRNAME,
    ResolvedPath,
    Workspace,
    WorkspaceError,
    WorkspaceNotAllowedError,
    WorkspaceNotFoundError,
    default_workspace_root,
    format_outside_display,
    is_path_within,
    relative_path_str,
)


@dataclass
class _DummyPolicySession:
    grants: set[str] = field(default_factory=set)
    granted_folders: set[Path] = field(default_factory=set)
    read_files: dict[Path, int] = field(default_factory=dict)


def test_workspace_resolves_inside_and_outside_paths(tmp_path: Path) -> None:
    """Relative and inside paths resolve with `inside=True`."""
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    readme = ws_dir / "README.md"
    readme.write_text("hello", encoding="utf-8")

    ws = Workspace(ws_dir)
    assert ws.root == ws_dir.resolve()
    assert ws.display_root == format_outside_display(ws_dir.resolve())

    res_rel = ws.resolve_safe("README.md")
    assert res_rel.path == readme.resolve()
    assert res_rel.inside is True
    assert res_rel.display == "README.md"
    assert res_rel.in_granted_folder is False

    res_root = ws.resolve_safe(".")
    assert res_root.path == ws.root
    assert res_root.inside is True
    assert res_root.display == "."


def test_sibling_prefix_folder_is_outside_workspace(tmp_path: Path) -> None:
    """Workspace `/tmp/ws`; `/tmp/ws-evil/x` is outside (`inside=False`)."""
    ws_dir = tmp_path / "ws"
    evil_dir = tmp_path / "ws-evil"
    ws_dir.mkdir()
    evil_dir.mkdir()
    evil_file = evil_dir / "x"
    evil_file.write_text("secret", encoding="utf-8")

    ws = Workspace(ws_dir)
    res = ws.resolve_safe(str(evil_file))
    assert res.inside is False
    assert res.path == evil_file.resolve()


def test_nonexistent_intermediate_dotdot_resolves_outside(tmp_path: Path) -> None:
    """`ws/sub/../../outside.txt` (non-existent `sub`) resolves outside."""
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    ws = Workspace(ws_dir)

    res = ws.resolve_safe("sub/../../outside.txt")
    assert res.inside is False
    assert res.path == (tmp_path / "outside.txt").resolve()


def test_symlink_inside_workspace_pointing_outside_counts_as_outside(
    tmp_path: Path,
) -> None:
    """Symlink inside workspace pointing outside resolves outside."""
    ws_dir = tmp_path / "ws"
    outside_dir = tmp_path / "outside"
    ws_dir.mkdir()
    outside_dir.mkdir()
    secret = outside_dir / "data.txt"
    secret.write_text("top secret", encoding="utf-8")

    link = ws_dir / "linked"
    link.symlink_to(outside_dir)

    ws = Workspace(ws_dir)
    res = ws.resolve_safe("linked/data.txt")
    assert res.inside is False
    assert res.path == secret.resolve()

    # Non-existent child under a symlink to an outside folder
    res_new = ws.resolve_safe("linked/new/sub/a.txt")
    assert res_new.inside is False
    assert res_new.path == (outside_dir.resolve() / "new" / "sub" / "a.txt")


def test_tilde_expansion_and_granted_folder_matching(tmp_path: Path) -> None:
    """`~` expands for current user and matches `granted_folders`."""
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    docs_dir = Path.home() / "Documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = docs_dir / "a.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")

    ws = Workspace(ws_dir)
    res1 = ws.resolve_safe("~/Documents/a.pdf")
    assert res1.inside is False
    assert res1.in_granted_folder is False
    assert res1.display == "~/Documents/a.pdf"

    res2 = ws.resolve_safe(
        "~/Documents/sub/b.pdf",
        granted_folders=[docs_dir.resolve()],
    )
    assert res2.inside is False
    assert res2.in_granted_folder is True
    assert res2.display == "~/Documents/sub/b.pdf"


def test_current_user_named_tilde_and_other_user_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`~<current_user>` works; `~otheruser` and `$VAR` are rejected."""
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    ws = Workspace(ws_dir)

    monkeypatch.setenv("USER", "folatest")
    res = ws.resolve_safe("~folatest/notes.txt")
    assert res.path == (Path.home().resolve() / "notes.txt")

    with pytest.raises(WorkspaceError, match="'~user' expansion"):
        ws.resolve_safe("~someone_else/notes.txt")

    with pytest.raises(WorkspaceError, match="environment variable"):
        ws.resolve_safe("$HOME/notes.txt")

    with pytest.raises(WorkspaceError, match="non-empty"):
        ws.resolve_safe("   ")

    with pytest.raises(WorkspaceError, match="null bytes"):
        ws.resolve_safe("a\x00b.txt")

    with pytest.raises(WorkspaceError, match="Expected a path string"):
        ws.resolve_safe(123)  # type: ignore[arg-type]


def test_workspace_root_validation_rejects_missing_broad_and_aldwin_home(
    tmp_path: Path,
    aldwin_home: Path,
) -> None:
    """Missing path, `/`, `~`, and `~/.aldwin` are rejected."""
    with pytest.raises(WorkspaceNotFoundError, match="workspace not found: /nope"):
        Workspace("/nope")

    with pytest.raises(WorkspaceNotFoundError, match="workspace not found"):
        Workspace("")

    with pytest.raises(WorkspaceNotFoundError, match="workspace not found"):
        Workspace("~other_invalid_user_xyz/ws")

    rel_ws = tmp_path / "rel_ws"
    rel_ws.mkdir()
    prev_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        ws_from_rel = Workspace("rel_ws")
        assert ws_from_rel.root == rel_ws.resolve()
    finally:
        os.chdir(prev_cwd)

    file_path = tmp_path / "a_file.txt"
    file_path.write_text("hi", encoding="utf-8")
    with pytest.raises(WorkspaceNotFoundError, match="workspace not found"):
        Workspace(file_path)

    with pytest.raises(WorkspaceNotAllowedError, match="workspace too broad: /"):
        Workspace("/")

    with pytest.raises(WorkspaceNotAllowedError, match="workspace too broad: ~"):
        Workspace("~")

    with pytest.raises(WorkspaceNotAllowedError, match="workspace not allowed"):
        Workspace(aldwin_home)

    with pytest.raises(WorkspaceNotAllowedError, match="workspace not allowed"):
        Workspace(aldwin_home / "skills")


def test_default_workspace_root_uses_cwd_when_it_is_allowed(
    tmp_path: Path, aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal project folder is used as the root without creating a fallback."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    assert default_workspace_root(aldwin_home) == project.resolve()
    assert not (aldwin_home.parent / DEFAULT_WORKSPACE_DIRNAME).exists()


def test_default_workspace_root_falls_back_when_cwd_is_user_home(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Launching from `~` yields a created `aldwin_workspace` beside the Aldwin home."""
    monkeypatch.chdir(Path.home())

    root = default_workspace_root(aldwin_home)

    expected = (aldwin_home.parent / DEFAULT_WORKSPACE_DIRNAME).resolve()
    assert root == expected
    assert root.is_dir()
    assert Workspace(root, home=aldwin_home).root == expected


def test_default_workspace_root_falls_back_when_cwd_is_inside_aldwin_home(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cwd inside `~/.aldwin` is never used as the root."""
    monkeypatch.chdir(aldwin_home)

    expected = (aldwin_home.parent / DEFAULT_WORKSPACE_DIRNAME).resolve()
    assert default_workspace_root(aldwin_home) == expected


def test_default_workspace_root_falls_back_when_cwd_was_deleted(
    tmp_path: Path, aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cwd removed after `cd` falls back instead of raising `FileNotFoundError`."""
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()

    expected = (aldwin_home.parent / DEFAULT_WORKSPACE_DIRNAME).resolve()
    assert default_workspace_root(aldwin_home) == expected
    assert expected.is_dir()


def test_default_workspace_root_reports_unwritable_fallback(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fallback folder that cannot be created raises a `WorkspaceError`."""
    monkeypatch.chdir(Path.home())

    def fail_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        raise PermissionError("read-only home")

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)

    with pytest.raises(WorkspaceNotFoundError, match="workspace not found"):
        default_workspace_root(aldwin_home)


def test_set_root_clears_all_grants_and_mark_read_records_mtime(
    tmp_path: Path,
) -> None:
    """`set_root` switches workspace and clears all session grants."""
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    ws1.mkdir()
    ws2.mkdir()
    note = ws1 / "note.md"
    note.write_text("v1", encoding="utf-8")

    session = _DummyPolicySession(
        grants={f"file:{note.resolve()}"},
        granted_folders={tmp_path},
    )
    ws = Workspace(ws1)
    marked = ws.mark_read("note.md", session=session)
    assert marked == note.resolve()
    assert session.read_files[marked] == note.stat().st_mtime_ns
    assert ws.read_files[marked] == note.stat().st_mtime_ns

    ws.set_root(ws2, session=session)
    assert ws.root == ws2.resolve()
    assert session.grants == set()
    assert session.granted_folders == set()
    assert ws.read_files == {}

    # Also test mark_read and set_root without passing a session object
    ws.mark_read(ws2 / "nonexistent.txt")
    ws.set_root(ws1)
    assert ws.root == ws1.resolve()


def test_symlink_loop_and_relative_symlinks_handled(tmp_path: Path) -> None:
    """Symlink loops raise `WorkspaceError`; relative symlinks resolve."""
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir()
    a = ws_dir / "loop_a"
    b = ws_dir / "loop_b"
    a.symlink_to("loop_b")
    b.symlink_to("loop_a")

    sub = ws_dir / "sub"
    sub.mkdir()
    target = sub / "real.txt"
    target.write_text("ok", encoding="utf-8")
    rel_link = ws_dir / "rel_link.txt"
    rel_link.symlink_to("./sub/real.txt")

    ws = Workspace(ws_dir)
    res = ws.resolve_safe("rel_link.txt")
    assert res.inside is True
    assert res.path == target.resolve()

    with pytest.raises(WorkspaceError, match="Symlink loop"):
        ws.resolve_safe("loop_a/file.txt")


def test_case_sensitivity_helpers(tmp_path: Path) -> None:
    """Case-insensitive vs case-sensitive component comparison."""
    parent = Path("/tmp/Workspace")
    child_same = Path("/tmp/workspace/ sub/file.txt".replace(" ", ""))
    outside = Path("/tmp/workspace-other/file.txt")

    assert is_path_within(child_same, parent, case_insensitive=True) is True
    assert is_path_within(child_same, parent, case_insensitive=False) is False
    assert is_path_within(outside, parent, case_insensitive=True) is False
    assert is_path_within(Path("/tmp"), parent, case_insensitive=True) is False
    assert relative_path_str(outside, parent, case_insensitive=True) is None
    assert format_outside_display(Path.home().resolve()) == "~"
    assert format_outside_display(Path("/etc/hosts")) == "/etc/hosts"
    _ = tmp_path


_SEGMENT_STRATEGY = st.sampled_from(
    [
        "a",
        "b",
        "..",
        ".",
        "README.md",
        ".env",
        "~",
        "$HOME",
        "~other",
        "ünîcødé",
        "linked_out",
        "linked_in",
        "\x00",
    ]
)


@given(
    segments=st.lists(_SEGMENT_STRATEGY, min_size=1, max_size=6),
    leading_slash=st.booleans(),
)
@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_hypothesis_resolve_safe_invariants(
    tmp_path: Path,
    segments: list[str],
    leading_slash: bool,
) -> None:
    """Hypothesis fuzzing of `Workspace.resolve_safe`."""
    ws_dir = tmp_path / "hyp_ws"
    out_dir = tmp_path / "hyp_out"
    ws_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)

    link_out = ws_dir / "linked_out"
    if not link_out.exists() and not link_out.is_symlink():
        link_out.symlink_to(out_dir)

    sub_in = ws_dir / "sub_in"
    sub_in.mkdir(exist_ok=True)
    link_in = ws_dir / "linked_in"
    if not link_in.exists() and not link_in.is_symlink():
        link_in.symlink_to(sub_in)

    ws = Workspace(ws_dir)
    raw = ("/" if leading_slash else "") + "/".join(segments)

    try:
        res = ws.resolve_safe(raw)
    except WorkspaceError:
        return

    assert isinstance(res, ResolvedPath)
    assert res.path.is_absolute()
    assert ".." not in res.path.parts
    expected_inside = is_path_within(res.path, ws.root)
    assert res.inside is expected_inside
    if res.inside:
        rel_parts = [p.casefold() for p in res.path.parts]
        root_parts = [p.casefold() for p in ws.root.parts]
        if os.name == "nt" or os.uname().sysname == "Darwin":
            assert rel_parts[: len(root_parts)] == root_parts
        else:
            assert res.path.is_relative_to(ws.root)
