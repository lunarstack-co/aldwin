"""Fixtures for the security suite."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
import yaml
from canary import CANARY

from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.policy import (
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
    wrap_tools_with_policy,
)
from aldwin.core.tools.calculate import build_calculate_tool
from aldwin.core.tools.documents import build_document_tools
from aldwin.core.tools.download import build_download_tool
from aldwin.core.tools.edits import build_edit_tools
from aldwin.core.tools.files import build_file_tools
from aldwin.core.tools.memory_tools import build_memory_tools
from aldwin.core.tools.run_command import build_run_command_tool
from aldwin.core.tools.skill_tools import build_create_skill_tool
from aldwin.core.tools.todo import build_todo_tool
from aldwin.core.workspace import Workspace


async def _noop_ask(_summary: str, _detail: str = "") -> bool:
    return True


@pytest.fixture
def canary_home(
    aldwin_home: Path,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Place the canary in secrets.yaml, workspace `.env`, and the environment."""
    secrets_path = aldwin_home / "secrets.yaml"
    raw: dict[str, Any] = {}
    if secrets_path.is_file():
        loaded = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
        if isinstance(loaded, dict):
            raw = loaded
    raw["canary"] = CANARY
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    secrets_path.chmod(0o600)

    env_file = workspace.root / ".env"
    env_file.write_text(f"SECRET={CANARY}\n", encoding="utf-8")

    monkeypatch.setenv("ALDWIN_CANARY", CANARY)
    monkeypatch.setenv("OPENAI_API_KEY", f"sk-test-{CANARY}")
    return aldwin_home


@pytest.fixture
def security_tools(
    workspace: Workspace,
    aldwin_home: Path,
) -> SimpleNamespace:
    """Policy-wrapped tools with a controllable ask callback."""
    session = PolicySession(workspace=workspace, home=aldwin_home)
    log = ApprovalLogger(home=aldwin_home)
    asks: list[ApprovalRequest] = []
    box: dict[str, Any] = {
        "answer": ApprovalAnswer("yes", "user"),
        "reason": None,
    }

    async def ask(req: ApprovalRequest) -> ApprovalAnswer:
        asks.append(req)
        answer = box["answer"]
        if box["reason"] is not None and answer.reason is None:
            return ApprovalAnswer(answer.answer, answer.source, reason=box["reason"])
        return answer

    raw = [
        *build_file_tools(workspace, session),
        *build_edit_tools(workspace, session),
        *build_document_tools(workspace, session),
        build_download_tool(workspace, session),
        *build_memory_tools(home=aldwin_home),
        build_todo_tool(),
        build_calculate_tool(),
        build_create_skill_tool(home=aldwin_home),
        build_run_command_tool(
            _noop_ask,
            cwd=workspace.root,
            wrapped_by_policy=True,
        ),
    ]
    wrapped = wrap_tools_with_policy(
        raw,
        session=session,
        ask=ask,
        approval_log=log,
    )
    return SimpleNamespace(
        root=workspace.root,
        home=aldwin_home,
        workspace=workspace,
        session=session,
        log=log,
        asks=asks,
        answer=box,
        tools={tool.name: tool for tool in wrapped},
    )


@pytest.fixture
def set_answer(security_tools: SimpleNamespace) -> Callable[..., None]:
    """Set the next approval answer on `security_tools`."""

    def _set(
        answer: Literal["yes", "yes_session", "no"] = "yes",
        *,
        reason: str | None = None,
    ) -> None:
        security_tools.answer["answer"] = ApprovalAnswer(
            answer,
            "user",
            reason=reason,
        )
        security_tools.answer["reason"] = reason

    return _set
