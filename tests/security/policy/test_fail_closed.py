"""Exceptions and unknown tools fail closed (deny, nothing runs)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.policy import (
    DENIAL_TEXT,
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
    PolicyWrappedTool,
    decide,
)
from aldwin.core.workspace import Workspace


@pytest.fixture
def policy_session(tmp_path: Path, aldwin_home: Path) -> PolicySession:
    root = tmp_path / "ws"
    root.mkdir()
    return PolicySession(workspace=Workspace(root, home=aldwin_home), home=aldwin_home)


class _Edit(Tool):
    name = "edit_file"
    description = "edit"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }
    calls: list[tuple[str, str]]

    def __init__(self) -> None:
        self.calls = []

    async def forward(self, path: str, content: str) -> str:  # type: ignore[override]
        self.calls.append((path, content))
        return "ok"


async def test_ask_exception_fails_closed(policy_session: PolicySession) -> None:
    """Exception in `ask` → policy_error; nothing runs."""
    tool = _Edit()

    async def boom(_req: ApprovalRequest) -> ApprovalAnswer:
        raise RuntimeError("ask exploded")

    wrapped = PolicyWrappedTool(tool, session=policy_session, ask=boom)
    with pytest.raises(ToolCallError) as caught:
        await wrapped.call({"path": "a.txt", "content": "x"})
    assert str(caught.value) == DENIAL_TEXT["policy_error"]
    assert tool.calls == []


def test_resolve_safe_exception_fails_closed(
    policy_session: PolicySession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exception in `resolve_safe` → deny source=error."""

    def boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("disk error")

    monkeypatch.setattr(policy_session.workspace, "resolve_safe", boom)
    dec = decide("read_file", {"path": "a.txt"}, policy_session)
    assert dec.kind == "deny"
    assert dec.source == "error"


def test_registry_lookup_exception_in_decide(
    policy_session: PolicySession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGISTRY access raising inside decide ends as deny source=error."""
    import aldwin.core.policy as policy_mod

    class BoomMap(dict):
        def get(self, key: object, default: object = None) -> object:
            raise RuntimeError("registry exploded")

        def __contains__(self, key: object) -> bool:
            raise RuntimeError("registry exploded")

    monkeypatch.setattr(policy_mod, "REGISTRY", BoomMap())
    dec = decide("read_file", {"path": "a.txt"}, policy_session)
    assert dec.kind == "deny"
    assert dec.source == "error"


async def test_runtime_unknown_tool_denied(policy_session: PolicySession) -> None:
    """Unregistered tool name at runtime is denied with policy_error text."""

    class _Rogue(Tool):
        name = "not_in_registry_xyz"
        description = "x"
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        async def forward(self) -> str:  # type: ignore[override]
            return "nope"

    async def ask(_req: ApprovalRequest) -> ApprovalAnswer:
        return ApprovalAnswer("yes", "user")

    wrapped = PolicyWrappedTool(_Rogue(), session=policy_session, ask=ask)
    with pytest.raises(ToolCallError) as caught:
        await wrapped.call({})
    assert str(caught.value) == DENIAL_TEXT["policy_error"]


async def test_forward_exception_leaves_file_unchanged(
    policy_session: PolicySession,
) -> None:
    """If `forward` raises after approval, the on-disk file is unchanged."""

    class _BoomEdit(Tool):
        name = "edit_file"
        description = "edit"
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        }

        async def forward(self, path: str, content: str) -> str:  # type: ignore[override]
            raise RuntimeError("disk full")

    async def ask(_req: ApprovalRequest) -> ApprovalAnswer:
        return ApprovalAnswer("yes", "user")

    path = policy_session.workspace.root / "a.txt"
    path.write_text("old\n", encoding="utf-8")
    policy_session.mark_read(path)
    wrapped = PolicyWrappedTool(_BoomEdit(), session=policy_session, ask=ask)
    with pytest.raises((ToolCallError, RuntimeError)):
        await wrapped.call({"path": "a.txt", "content": "new\n"})
    assert path.read_text(encoding="utf-8") == "old\n"
