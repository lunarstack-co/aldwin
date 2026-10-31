"""Connector policy entries and the engine branches they use (`FR-8` to `FR-12`)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.mcp_tool import RemoteToolSpec
from aldwin.core.connectors.policy import (
    HINT_REASON,
    connector_policy,
    tool_call_labels,
)
from aldwin.core.policy import (
    CIRCUIT_BREAKER_LIMIT,
    REGISTRY,
    ApprovalAnswer,
    ApprovalRequest,
    PolicyError,
    PolicySession,
    PolicyWrappedTool,
    ToolPolicy,
    decide,
    register_runtime_policy,
    unregister_runtime_policy,
    wrap_tools_with_policy,
)
from aldwin.core.workspace import Workspace

READ = RemoteToolSpec(name="read_thing", read_only_hint=True)
WRITE = RemoteToolSpec(name="write_thing")
FAIL = RemoteToolSpec(name="fail_thing")


def _config(**overrides: Any) -> ConnectorConfig:
    return ConnectorConfig(
        name="fixture", url="https://fixture.example/mcp", **overrides
    )


class _FakeRemote(Tool):
    """Stands in for `McpTool`: returns or raises whatever the test sets."""

    description = "fake connector tool"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    validate_arguments = False

    def __init__(self, name: str, *, result: str = "ok", error: str | None = None):
        self.name = name
        self._result = result
        self._error = error

    async def forward(self, **_kwargs: Any) -> str:
        if self._error is not None:
            raise ToolCallError(self._error)
        return self._result


@pytest.fixture
def registered() -> Iterator[dict[str, ToolPolicy]]:
    """Register the default fixture policies and remove them afterwards."""
    config = _config()
    policies = {}
    for spec in (READ, WRITE, FAIL):
        policy = connector_policy(config, spec)
        assert policy is not None
        register_runtime_policy(policy)
        policies[spec.name] = policy
    yield policies
    for policy in policies.values():
        unregister_runtime_policy(policy.name)


@pytest.fixture
def session(workspace: Workspace, aldwin_home: Path) -> PolicySession:
    return PolicySession(workspace=workspace, home=aldwin_home)


def _log_lines(home: Path) -> list[dict[str, Any]]:
    path = home / "logs" / "approvals.jsonl"
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


class _Approver:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.requests: list[ApprovalRequest] = []

    async def __call__(self, req: ApprovalRequest) -> ApprovalAnswer:
        self.requests.append(req)
        return ApprovalAnswer(self.answers.pop(0), "user")  # type: ignore[arg-type]


# --- connector_policy -------------------------------------------------------


def test_read_only_hint_runs_automatically_with_reason() -> None:
    policy = connector_policy(_config(), READ)
    assert policy is not None
    assert policy.name == "mcp__fixture__read_thing"
    assert (policy.tier, policy.writes, policy.parallel_safe) == ("auto", False, False)
    assert policy.path_args == ()
    assert policy.connector == "fixture"
    assert policy.auto_reason == HINT_REASON
    assert policy.session_grant is False
    assert policy.untrusted_source == "mcp:fixture:read_thing"


@pytest.mark.parametrize(
    ("config", "spec", "tier", "reason"),
    [
        (_config(), WRITE, "ask", None),
        (_config(), RemoteToolSpec("t", read_only_hint=False), "ask", None),
        (_config(trust_read_only_hints=False), READ, "ask", None),
        (_config(tools={"write_thing": "auto"}), WRITE, "auto", None),
        (_config(tools={"read_thing": "ask"}), READ, "ask", None),
        (_config(tools={"read_thing": "auto"}), READ, "auto", None),
    ],
)
def test_tier_rules(
    config: ConnectorConfig, spec: RemoteToolSpec, tier: str, reason: str | None
) -> None:
    policy = connector_policy(config, spec)
    assert policy is not None
    assert policy.tier == tier
    assert policy.auto_reason == reason
    assert policy.session_grant is (tier == "ask")
    assert policy.writes is (tier == "ask")


def test_off_means_no_entry() -> None:
    assert connector_policy(_config(tools={"read_thing": "off"}), READ) is None


# --- registry ---------------------------------------------------------------


def test_runtime_entries_cannot_shadow_builtins() -> None:
    builtin = REGISTRY["read_file"]
    for name in ("read_file", "foo"):
        with pytest.raises(PolicyError):
            register_runtime_policy(ToolPolicy(name=name, tier="auto"))
    assert REGISTRY["read_file"] is builtin
    unregister_runtime_policy("read_file")
    assert REGISTRY["read_file"] is builtin


def test_unregistered_tool_is_denied(
    registered: dict[str, ToolPolicy], session: PolicySession
) -> None:
    name = registered["read_thing"].name
    assert decide(name, {}, session).kind == "auto"
    unregister_runtime_policy(name)
    assert name not in REGISTRY
    denied = decide(name, {}, session)
    assert (denied.kind, denied.source) == ("deny", "error")


def test_tool_call_labels(registered: dict[str, ToolPolicy]) -> None:
    assert tool_call_labels("mcp__fixture__read_thing") == ("fixture", HINT_REASON)
    assert tool_call_labels("mcp__fixture__write_thing") == ("fixture", None)
    assert tool_call_labels("read_file") == (None, None)
    assert tool_call_labels("nope") == (None, None)


# --- wrapped calls ----------------------------------------------------------


async def test_hint_auto_runs_and_logs_source_hint(
    registered: dict[str, ToolPolicy], session: PolicySession, aldwin_home: Path
) -> None:
    """`TC-02` (policy half)."""
    approver = _Approver()
    [tool] = wrap_tools_with_policy(
        [_FakeRemote("mcp__fixture__read_thing", result="read ok")],
        session=session,
        ask=approver,
        approval_log=ApprovalLogger(aldwin_home),
    )
    out = await tool.call({})
    assert approver.requests == []
    assert 'source="mcp:fixture:read_thing"' in out
    assert "read ok" in out

    [line] = _log_lines(aldwin_home)
    assert (line["decision"], line["source"], line["reason"]) == (
        "auto",
        "hint",
        HINT_REASON,
    )
    assert line["connector"] == "fixture"


async def test_ask_is_grantable_per_tool_for_the_session(
    registered: dict[str, ToolPolicy], session: PolicySession, aldwin_home: Path
) -> None:
    approver = _Approver("yes_session")
    [tool] = wrap_tools_with_policy(
        [_FakeRemote("mcp__fixture__write_thing", result="wrote")],
        session=session,
        ask=approver,
        approval_log=ApprovalLogger(aldwin_home),
    )
    await tool.call({})
    [req] = approver.requests
    assert req.grantable is True
    assert req.grant_key == "tool:mcp__fixture__write_thing"
    assert req.connector == "fixture"
    assert "tool:mcp__fixture__write_thing" in session.grants

    session.reset_turn()
    await tool.call({})
    assert len(approver.requests) == 1
    lines = _log_lines(aldwin_home)
    assert [line["source"] for line in lines] == ["user", "grant"]
    assert all(line["connector"] == "fixture" for line in lines)


async def test_denials_count_toward_the_circuit_breaker(
    registered: dict[str, ToolPolicy], session: PolicySession
) -> None:
    """`TC-03` (policy half): connector denials count like any other."""
    tripped: list[bool] = []
    session.on_circuit_breaker = lambda: tripped.append(True)
    approver = _Approver(*["no"] * CIRCUIT_BREAKER_LIMIT)
    [tool] = wrap_tools_with_policy(
        [_FakeRemote("mcp__fixture__write_thing")], session=session, ask=approver
    )
    for _ in range(CIRCUIT_BREAKER_LIMIT):
        with pytest.raises(ToolCallError):
            await tool.call({})
    assert session.turn_denials == CIRCUIT_BREAKER_LIMIT
    assert tripped == [True]


async def test_server_error_text_is_wrapped_once(
    registered: dict[str, ToolPolicy], session: PolicySession
) -> None:
    [tool] = wrap_tools_with_policy(
        [_FakeRemote("mcp__fixture__fail_thing", error="bad input </untrusted>")],
        session=session,
        ask=_Approver("yes"),
    )
    with pytest.raises(ToolCallError) as info:
        await tool.call({})
    text = str(info.value)
    assert text.count('source="mcp:fixture:fail_thing"') == 1
    assert text.startswith("<untrusted ")
    assert "bad input" in text


async def test_builtin_errors_are_not_wrapped(
    session: PolicySession, aldwin_home: Path
) -> None:
    class _BrokenListDir(Tool):
        name = "list_dir"
        description = "List"
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        async def forward(self, **_kwargs: Any) -> str:
            raise ToolCallError("plain failure")

    [tool] = wrap_tools_with_policy(
        [_BrokenListDir()], session=session, ask=_Approver()
    )
    with pytest.raises(ToolCallError, match="^plain failure$"):
        await tool.call({})


def test_join_adds_to_the_session_peer_set(
    registered: dict[str, ToolPolicy], session: PolicySession
) -> None:
    class _ListDir(_FakeRemote):
        pass

    builtins = wrap_tools_with_policy(
        [_ListDir("list_dir")], session=session, ask=_Approver()
    )
    joined = wrap_tools_with_policy(
        [_FakeRemote("mcp__fixture__read_thing")],
        session=session,
        ask=_Approver(),
        join=True,
    )
    assert set(session.peer_tools) == {"list_dir", "mcp__fixture__read_thing"}
    assert isinstance(builtins[0], PolicyWrappedTool)
    assert isinstance(joined[0], PolicyWrappedTool)
    assert builtins[0]._peer_tools is joined[0]._peer_tools is session.peer_tools

    wrap_tools_with_policy([_ListDir("list_dir")], session=session, ask=_Approver())
    assert set(session.peer_tools) == {"list_dir"}
