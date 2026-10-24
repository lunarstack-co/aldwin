"""Path escapes never return outside-workspace content without approval."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from aldwin.core.policy import DENIAL_TEXT
from aldwin.core.workspace import Workspace, WorkspaceError
from security._helpers import call_fail


async def test_outside_relative_read_declined(
    security_tools: SimpleNamespace,
    set_answer,
    tmp_path: Path,
) -> None:
    """Outside relative read asks; on `n`, user denial text; no body."""
    outside = tmp_path / "secret.txt"
    outside.write_text("LEAK\n", encoding="utf-8")
    set_answer("no")
    msg = await call_fail(
        security_tools.tools["read_file"],
        {"path": "../secret.txt"},
    )
    assert msg == DENIAL_TEXT["user"]
    assert "LEAK" not in msg
    assert len(security_tools.asks) == 1


async def test_absolute_etc_passwd_asks_or_blocks(
    security_tools: SimpleNamespace,
    set_answer,
) -> None:
    """Absolute `/etc/passwd` never returns content without approval."""
    set_answer("no")
    msg = await call_fail(
        security_tools.tools["read_file"],
        {"path": "/etc/passwd"},
    )
    assert "root:" not in msg
    assert msg in (
        DENIAL_TEXT["user"],
        DENIAL_TEXT["blocked_path"].format(path="/etc/passwd"),
    ) or msg.startswith(DENIAL_TEXT["user"].split(".")[0])


async def test_tilde_home_path_asks(
    security_tools: SimpleNamespace,
    set_answer,
    tmp_path: Path,
) -> None:
    """`~/x` resolves under the test HOME and still requires approval."""
    target = Path.home() / "notes.txt"
    target.write_text("home-note\n", encoding="utf-8")
    set_answer("no")
    msg = await call_fail(
        security_tools.tools["read_file"],
        {"path": "~/notes.txt"},
    )
    assert msg == DENIAL_TEXT["user"]
    assert "home-note" not in msg


async def test_sibling_workspace_not_covered_by_prefix(
    security_tools: SimpleNamespace,
    set_answer,
    tmp_path: Path,
    aldwin_home: Path,
) -> None:
    """`/ws` grant must not cover `/ws-evil` (never `startswith`)."""
    evil = tmp_path / "ws-evil"
    evil.mkdir()
    (evil / "secret.txt").write_text("SIBLING\n", encoding="utf-8")
    # Grant the real workspace folder only.
    security_tools.session.granted_folders.add(security_tools.workspace.root)
    set_answer("no")
    msg = await call_fail(
        security_tools.tools["read_file"],
        {"path": str(evil / "secret.txt")},
    )
    assert "SIBLING" not in msg
    assert msg == DENIAL_TEXT["user"]


async def test_symlink_walk_outside_not_followed(
    security_tools: SimpleNamespace,
    tmp_path: Path,
) -> None:
    """`list_dir` / `grep` do not follow a dir symlink out of the workspace."""
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "leak.txt").write_text("OUT\n", encoding="utf-8")
    link = security_tools.root / "escape"
    link.symlink_to(outside)
    listed = await security_tools.tools["list_dir"].call({"path": ".", "depth": 2})
    body = str(listed)
    assert "leak.txt" not in body
    assert "OUT" not in body
    grepped = await security_tools.tools["grep"].call({"pattern": "OUT", "path": "."})
    assert "OUT" not in str(grepped) or "no matches" in str(grepped).lower()


async def test_glob_and_grep_reject_dotdot_patterns(
    security_tools: SimpleNamespace,
) -> None:
    """Patterns with `..` or leading `/` are refused before search."""
    expected = (
        "Not run: the search pattern must stay inside its folder. "
        "This can't be approved."
    )
    for tool_name, args in (
        ("glob", {"pattern": "../**/*.md"}),
        ("glob", {"pattern": "/etc/*"}),
        ("grep", {"pattern": "x", "glob": "../*"}),
    ):
        security_tools.session.reset_turn()
        msg = await call_fail(security_tools.tools[tool_name], args)
        assert msg == expected


@pytest.mark.skipif(sys.platform != "darwin", reason="case-fold floor is Darwin-only")
async def test_macos_case_env_blocked(security_tools: SimpleNamespace) -> None:
    """On macOS, `.ENV` is blocked like `.env`."""
    (security_tools.root / ".ENV").write_text("CASE=1\n", encoding="utf-8")
    msg = await call_fail(
        security_tools.tools["read_file"],
        {"path": ".ENV"},
    )
    assert "may hold secrets" in msg
    assert "CASE=1" not in msg


async def test_folder_grant_covers_subfolders_not_siblings(
    security_tools: SimpleNamespace,
    set_answer,
    tmp_path: Path,
) -> None:
    """folder grant covers children; sibling folders re-ask."""
    parent = tmp_path / "docs"
    child = parent / "a"
    sibling = tmp_path / "other"
    child.mkdir(parents=True)
    sibling.mkdir()
    (child / "one.txt").write_text("one\n", encoding="utf-8")
    (sibling / "two.txt").write_text("two\n", encoding="utf-8")

    set_answer("yes")
    first = await security_tools.tools["read_file"].call(
        {"path": str(child / "one.txt")}
    )
    assert "one" in str(first)
    asks_after_first = len(security_tools.asks)

    # Same granted folder: child file should not re-ask if grant was folder-scoped.
    second = await security_tools.tools["read_file"].call(
        {"path": str(child / "one.txt")}
    )
    assert "one" in str(second)

    set_answer("no")
    sibling_msg = await call_fail(
        security_tools.tools["read_file"],
        {"path": str(sibling / "two.txt")},
    )
    assert sibling_msg == DENIAL_TEXT["user"]
    assert "two" not in sibling_msg
    assert len(security_tools.asks) > asks_after_first


@given(st.text(min_size=1, max_size=40))
@settings(
    max_examples=40,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
    deadline=None,
)
def test_hypothesis_resolve_safe_never_escapes_via_dotdot(
    security_tools: SimpleNamespace,
    payload: str,
) -> None:
    """Hypothesis: random segments with `..` never resolve inside via escape."""
    ws: Workspace = security_tools.workspace
    candidate = f"../{payload}/x"
    try:
        resolved = ws.resolve_safe(candidate)
    except (WorkspaceError, OSError, ValueError):
        return
    if resolved.inside:
        assert ws.root in resolved.path.parents or resolved.path == ws.root
    else:
        assert ws.root not in resolved.path.parents and resolved.path != ws.root
