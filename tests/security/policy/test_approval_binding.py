"""digest binding, grants, workspace clear, never grantable cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from aldwin.core.policy import (
    PolicySession,
    compute_digest,
    decide,
)
from aldwin.core.workspace import Workspace


@pytest.fixture
def session(tmp_path: Path, aldwin_home: Path) -> PolicySession:
    root = tmp_path / "ws"
    root.mkdir()
    return PolicySession(workspace=Workspace(root, home=aldwin_home), home=aldwin_home)


def test_build_files_never_grantable(session: PolicySession) -> None:
    """BUILD_FILES Diff asks but are never grantable."""
    (session.workspace.root / "Makefile").write_text("all:\n", encoding="utf-8")
    session.mark_read(session.workspace.root / "Makefile")
    d = decide(
        "write_file",
        {"path": "Makefile", "content": "x\n"},
        session,
    )
    assert d.kind == "diff"
    assert d.grantable is False


def test_outside_write_never_grantable(session: PolicySession, tmp_path: Path) -> None:
    """Outside-workspace writes always ask and are never grantable."""
    outside = tmp_path / "out.txt"
    outside.write_text("a\n", encoding="utf-8")
    session.mark_read(outside)
    d = decide(
        "write_file",
        {"path": str(outside), "content": "b\n"},
        session,
    )
    assert d.kind in ("diff", "ask")
    assert d.grantable is False


def test_workspace_change_clears_grants(session: PolicySession, tmp_path: Path) -> None:
    """Switching workspace root clears session grants."""
    session.grants.add("file:/tmp/x")
    session.granted_folders.add(tmp_path)
    new_root = tmp_path / "other"
    new_root.mkdir()
    session.workspace.set_root(new_root, session=session)
    assert session.grants == set()
    assert session.granted_folders == set()


def test_digest_binds_tool_and_detail() -> None:
    """Different detail strings produce different digests for the same tool."""
    a = compute_digest("write_file", {"path": "a.txt"}, "one\n")
    b = compute_digest("write_file", {"path": "a.txt"}, "two\n")
    assert a != b
