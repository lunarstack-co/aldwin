"""write rules - outside never grantable, read-before-write, atomic, recheck."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.policy import (
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
    wrap_tools_with_policy,
)
from aldwin.core.tools.edits import EditFileTool
from aldwin.core.tools.files import ReadFileTool
from aldwin.core.workspace import Workspace
from security._helpers import call_fail


async def _mark_read(tools: SimpleNamespace, rel: str) -> None:
    result = await tools.tools["read_file"].call({"path": rel})
    assert result.ok is True, result


async def test_outside_edit_never_grantable(
    security_tools: SimpleNamespace,
    set_answer,
    tmp_path: Path,
) -> None:
    """Outside-workspace edits always ask and are never grantable."""
    outside = tmp_path / "outside.md"
    outside.write_text("old\n", encoding="utf-8")
    set_answer("yes")
    security_tools.session.mark_read(outside)
    out = await security_tools.tools["write_file"].call(
        {"path": str(outside), "content": "new\n"}
    )
    assert out.ok is True
    assert security_tools.asks
    assert security_tools.asks[-1].grantable is False
    assert outside.read_text(encoding="utf-8") == "new\n"


async def test_overwrite_without_read_refused(
    security_tools: SimpleNamespace,
) -> None:
    """Overwriting a file not read this session is refused."""
    path = security_tools.root / "a.txt"
    path.write_text("keep\n", encoding="utf-8")
    msg = await call_fail(
        security_tools.tools["write_file"],
        {"path": "a.txt", "content": "overwrite\n"},
    )
    assert msg == "read the file first: a.txt"
    assert path.read_text(encoding="utf-8") == "keep\n"
    assert security_tools.asks == []


async def test_stale_mtime_after_read_refused(
    security_tools: SimpleNamespace,
) -> None:
    """A file changed on disk after reading is refused."""
    path = security_tools.root / "a.txt"
    path.write_text("hello\n", encoding="utf-8")
    await _mark_read(security_tools, "a.txt")
    path.write_text("hello\nchanged\n", encoding="utf-8")
    msg = await call_fail(
        security_tools.tools["edit_file"],
        {"path": "a.txt", "old": "hello", "new": "hi"},
    )
    assert msg == "file changed since last read; read it again"
    assert security_tools.asks == []


async def test_atomic_write_failure_leaves_original(
    security_tools: SimpleNamespace,
) -> None:
    """Mid-write OSError leaves the original file intact."""
    path = security_tools.root / "a.txt"
    path.write_text("original\n", encoding="utf-8")
    await _mark_read(security_tools, "a.txt")

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    with patch("aldwin.core.tools.edits.os.replace", side_effect=boom):
        msg = await call_fail(
            security_tools.tools["write_file"],
            {"path": "a.txt", "content": "new\n"},
        )
    assert "failed to write" in msg or "disk full" in msg
    assert path.read_text(encoding="utf-8") == "original\n"


async def test_mtime_change_between_approval_and_run(
    workspace: Workspace,
    aldwin_home: Path,
) -> None:
    """File changed after yes: recheck soft-fails; original mutation kept, no edit."""
    path = workspace.root / "a.md"
    path.write_text("v1\n", encoding="utf-8")
    session = PolicySession(workspace=workspace, home=aldwin_home)
    log = ApprovalLogger(home=aldwin_home)
    asks: list[ApprovalRequest] = []

    async def mutating_ask(req: ApprovalRequest) -> ApprovalAnswer:
        asks.append(req)
        path.write_text("v1-changed\n", encoding="utf-8")
        return ApprovalAnswer("yes", "user")

    wrapped = wrap_tools_with_policy(
        [
            ReadFileTool(workspace, session),
            EditFileTool(workspace, session),
        ],
        session=session,
        ask=mutating_ask,
        approval_log=log,
    )
    tools = {tool.name: tool for tool in wrapped}
    read = await tools["read_file"].call({"path": "a.md"})
    assert read.ok is True
    msg = await call_fail(
        tools["edit_file"],
        {"path": "a.md", "old": "v1", "new": "v2"},
    )
    assert len(asks) == 1
    assert msg == "file changed since last read; read it again"
    assert path.read_text(encoding="utf-8") == "v1-changed\n"
