"""Policy wrapper display side-channel tests (`record_display`, `take_display`)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.policy import (
    DENIAL_TEXT,
    ApprovalPreview,
    ApprovalRequest,
    PolicySession,
    evaluate_step_batch,
    wrap_tools_with_policy,
)
from aldwin.core.tools import ToolOutput
from aldwin.core.tools import display as core_display
from aldwin.core.workspace import Workspace


@pytest.fixture
def session(workspace: Workspace, aldwin_home: Path) -> PolicySession:
    return PolicySession(workspace=workspace, home=aldwin_home)


class _StubRunCommand(Tool):
    name = "run_command"
    description = "Stub command runner."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }

    def __init__(
        self,
        *,
        exit_code: int = 0,
        ok: bool = True,
        bad_display: Any = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._exit_code = exit_code
        self._ok = ok
        self._bad_display = bad_display
        self._raise_exc = raise_exc

    async def forward(self, command: str) -> ToolOutput:  # type: ignore[override]
        if self._raise_exc is not None:
            raise self._raise_exc
        disp = (
            self._bad_display
            if self._bad_display is not None
            else core_display.terminal(
                command=command,
                cwd="/ws",
                stdout="out\n",
                exit_code=self._exit_code,
                duration_ms=5,
            )
        )
        return ToolOutput(
            "out\n",
            summary="ran",
            ok=self._ok,
            duration_ms=5,
            display=disp,
        )


class _StubEditFile(Tool):
    name = "edit_file"
    description = "Stub file editor."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    def prepare_approval(self, path: str) -> ApprovalPreview:
        return ApprovalPreview(
            summary=f"Edit {path}",
            detail="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
            display=core_display.diff(
                path=path,
                unified="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n",
            ),
        )

    async def forward(self, path: str) -> ToolOutput:  # type: ignore[override]
        return ToolOutput("edited", summary=f"Edit {path}", ok=True)


class _StubReadFile(Tool):
    name = "read_file"
    description = "Stub file reader."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def forward(self, path: str) -> ToolOutput:  # type: ignore[override]
        return ToolOutput(
            f"read {path}",
            summary=f"Read {path}",
            ok=True,
            display={"kind": "files", "root": path, "entries": [], "truncated": False},
        )


async def test_display_taken_once_on_success(
    session: PolicySession,
    approver: Callable[..., Any],
) -> None:
    """`take_display` returns the recorded dict once, then `None`."""
    wrapped = wrap_tools_with_policy(
        [_StubRunCommand()], session=session, ask=approver("yes")
    )[0]
    session.enqueue_step_call("c_ok", "run_command", {"command": "echo hi"})
    result = await wrapped.call({"command": "echo hi"})
    assert isinstance(result, ToolOutput)

    first = session.take_display("c_ok")
    assert first is not None
    assert first["kind"] == "terminal"
    assert first["command"] == "echo hi"
    assert session.take_display("c_ok") is None


async def test_display_survives_failed_tool(
    session: PolicySession,
    approver: Callable[..., Any],
) -> None:
    """A tool returning `ok=False` raises `ToolCallError` while keeping its display."""
    wrapped = wrap_tools_with_policy(
        [_StubRunCommand(exit_code=1, ok=False)],
        session=session,
        ask=approver("yes"),
    )[0]
    session.enqueue_step_call("c_fail", "run_command", {"command": "false"})
    with pytest.raises(ToolCallError):
        await wrapped.call({"command": "false"})

    taken = session.take_display("c_fail")
    assert taken is not None
    assert taken["kind"] == "terminal"
    assert taken["exit_code"] == 1


async def test_rule_denial_records_denied_display(
    session: PolicySession,
    approver: Callable[..., Any],
) -> None:
    """A hardline command refusal records a `denied` display with `blocked_command`."""
    wrapped = wrap_tools_with_policy(
        [_StubRunCommand()], session=session, ask=approver("yes")
    )[0]
    session.enqueue_step_call("c_rule", "run_command", {"command": "rm -rf /"})
    with pytest.raises(ToolCallError):
        await wrapped.call({"command": "rm -rf /"})

    taken = session.take_display("c_rule")
    assert taken == {
        "kind": "denied",
        "rule": "blocked_command",
        "message": DENIAL_TEXT["blocked_command"].format(rule="rm_root_or_system"),
    }


async def test_user_denial_records_user_and_user_edit_rules(
    session: PolicySession,
    approver: Callable[..., Any],
) -> None:
    """Saying `no` records `user` for commands and `user_edit` for file edits."""
    (session.workspace.root / "notes.md").write_text("old\n", encoding="utf-8")
    cmd_tool, edit_tool = wrap_tools_with_policy(
        [_StubRunCommand(), _StubEditFile()],
        session=session,
        ask=approver("no", reason="not now"),
    )

    session.enqueue_step_call("c_cmd", "run_command", {"command": "ls"})
    with pytest.raises(ToolCallError):
        await cmd_tool.call({"command": "ls"})
    assert session.take_display("c_cmd") == {
        "kind": "denied",
        "rule": "user",
        "message": DENIAL_TEXT["user"],
    }

    session.clear_step_calls()
    session.enqueue_step_call("c_edit", "edit_file", {"path": "notes.md"})
    with pytest.raises(ToolCallError):
        await edit_tool.call({"path": "notes.md"})
    assert session.take_display("c_edit") == {
        "kind": "denied",
        "rule": "user_edit",
        "message": DENIAL_TEXT["user_edit"],
    }


async def test_grant_display_on_grantable_ask_and_call_id_on_request(
    tmp_path: Path,
    session: PolicySession,
    approver: Callable[..., Any],
) -> None:
    """Outside-workspace reads send `grant` display; `run_command` keeps `terminal`."""
    outside_dir = (tmp_path / "outside_docs").resolve()
    outside_dir.mkdir()
    outside_file = outside_dir / "tax.txt"
    outside_file.write_text("hello\n", encoding="utf-8")

    asks: list[ApprovalRequest] = []
    read_tool, cmd_tool = wrap_tools_with_policy(
        [_StubReadFile(), _StubRunCommand()],
        session=session,
        ask=approver("yes", asks=asks),
    )

    session.enqueue_step_call("c_out", "read_file", {"path": str(outside_file)})
    await read_tool.call({"path": str(outside_file)})
    assert len(asks) == 1
    assert asks[0].call_id == "c_out"
    assert asks[0].display == {
        "kind": "grant",
        "scope": "folder",
        "reach": [str(outside_dir)],
    }

    session.clear_step_calls()
    asks.clear()
    session.enqueue_step_call("c_run", "run_command", {"command": "pwd"})
    await cmd_tool.call({"command": "pwd"})
    assert len(asks) == 1
    assert asks[0].call_id == "c_run"
    assert asks[0].display is not None
    assert asks[0].display["kind"] == "terminal"
    assert asks[0].display["command"] == "pwd"


async def test_bad_display_or_tool_exception_never_alters_decision(
    session: PolicySession,
    approver: Callable[..., Any],
) -> None:
    """Non-dict displays are ignored; runtime exceptions fail identically to today."""
    bad_disp_tool = wrap_tools_with_policy(
        [_StubRunCommand(bad_display="oops")],
        session=session,
        ask=approver("yes"),
    )[0]
    session.enqueue_step_call("c_bad", "run_command", {"command": "echo ok"})
    out = await bad_disp_tool.call({"command": "echo ok"})
    assert "out" in str(out)
    assert session.take_display("c_bad") is None

    session.clear_step_calls()
    boom_tool = wrap_tools_with_policy(
        [_StubRunCommand(raise_exc=ValueError("boom"))],
        session=session,
        ask=approver("yes"),
    )[0]
    session.enqueue_step_call("c_boom", "run_command", {"command": "echo ok"})
    with pytest.raises(ValueError, match="boom"):
        await boom_tool.call({"command": "echo ok"})
    assert session.take_display("c_boom") is None


async def test_no_leak_between_parallel_calls(
    session: PolicySession,
    approver: Callable[..., Any],
) -> None:
    """Each `call_id` in a multi-call step receives only its own display."""
    f1 = session.workspace.root / "a.txt"
    f2 = session.workspace.root / "b.txt"
    f1.write_text("a\n", encoding="utf-8")
    f2.write_text("b\n", encoding="utf-8")

    read_tool = wrap_tools_with_policy(
        [_StubReadFile()], session=session, ask=approver("yes")
    )[0]
    session.enqueue_step_call("c_1", "read_file", {"path": "a.txt"})
    session.enqueue_step_call("c_2", "read_file", {"path": "b.txt"})
    await evaluate_step_batch(session, {"read_file": read_tool}, approver("yes"))

    await read_tool.call({"path": "a.txt"})
    await read_tool.call({"path": "b.txt"})

    d1 = session.take_display("c_1")
    d2 = session.take_display("c_2")
    assert d1 is not None and d1["root"] == "a.txt"
    assert d2 is not None and d2["root"] == "b.txt"
    assert session.take_display("unknown_call") is None
