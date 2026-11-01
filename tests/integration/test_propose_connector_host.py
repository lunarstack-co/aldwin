"""Asking Aldwin to connect: a scripted model calls `propose_connector` through
a real host, and the user answers the approval and any sign-in prompt.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from connectors._proc import fixture_argv
from fakes.model import FakeModel, final, tool_call
from fixtures.mcp_server import FixtureControls, build_fixture_server

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.connectors import stdio
from aldwin.core.connectors.catalog import CatalogEntry
from aldwin.core.connectors.config import (
    ConnectorConfig,
    connectors_path,
    load_connectors,
    save_connectors,
)
from aldwin.core.connectors.connection import McpConnection
from aldwin.core.connectors.credentials import FileCredentialStore
from aldwin.core.connectors.errors import (
    ConnectorAuthRequired,
    ConnectorCommandNotFound,
    ConnectorNotApproved,
    ConnectorUnreachable,
    NeedsSignIn,
    SignInCancelled,
    SignInTimedOut,
)
from aldwin.core.connectors.proposal import ConnectorProposal
from aldwin.core.policy import DENIAL_TEXT
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.workspace import Workspace
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.host.connectors import AddResult
from aldwin.protocol import (
    ApprovalEvent,
    ConnectorAuthEvent,
    DoneEvent,
    Event,
    ToolResultEvent,
)

TOKEN = "tok-propose-canary-41f9"
CATALOG = (
    CatalogEntry(
        name="plain",
        title="Plain",
        purpose="A server that needs no sign-in",
        url="https://plain.example/mcp",
        auth="none",
        docs_url="https://plain.example",
    ),
    CatalogEntry(
        name="gated",
        title="Gated",
        purpose="A server that needs a token",
        url="https://gated.example/mcp",
        auth="bearer",
        docs_url="https://gated.example",
        token_help_url="https://gated.example/tokens",
    ),
)


class _NeedsToken:
    """A connection to a server that answers 401 until a token is sent."""

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    async def open(self, *, timeout: float = 30.0) -> list[Any]:
        raise ConnectorAuthRequired("gated.example")

    async def close(self, *, timeout: float | None = None) -> None:
        return None


def _factory(controls: FixtureControls) -> Any:
    def factory(config: ConnectorConfig, **kwargs: Any) -> Any:
        if config.kind == "local":
            return McpConnection(config, **kwargs)
        if config.name == "gated" and kwargs.get("auth") is None:
            return _NeedsToken(config)
        return McpConnection(
            config, transport_factory=lambda _http: controls.server, **kwargs
        )

    return factory


@dataclass
class Rig:
    host: AldwinHost
    client: AldwinClient
    model: FakeModel
    home: Path

    @property
    def manager(self) -> Any:
        return self.host._connectors

    async def turn(
        self,
        text: str,
        *,
        answer: str = "yes",
        on_auth: Callable[[ConnectorAuthEvent], Awaitable[None]] | None = None,
    ) -> list[Event]:
        events: list[Event] = []
        async for ev in self.client.turn(text):
            events.append(ev)
            if isinstance(ev, ApprovalEvent):
                await self.client.approve(ev.approval_id, answer, digest=ev.digest)  # type: ignore[arg-type]
            elif isinstance(ev, ConnectorAuthEvent) and on_auth is not None:
                await on_auth(ev)
        return events

    def tool_text(self) -> str:
        """What the model was given back for its last tool call."""
        messages = self.model.calls[-1]
        tool_messages = [m for m in messages if m.role == "tool"]
        return str(tool_messages[-1].content)


RigFactory = Callable[[], Awaitable[Rig]]


@pytest.fixture
async def make_rig(
    fake_model: FakeModel, aldwin_home: Path, workspace: Workspace
) -> AsyncIterator[RigFactory]:
    rigs: list[Rig] = []

    async def make() -> Rig:
        holder: list[AldwinHost] = []

        def builder(model_name: str, ask: Any) -> Any:
            return build_agent(
                AldwinConfig(model=model_name),
                SecretsConfig(),
                ask,
                home=aldwin_home,
                model=fake_model,
                connectors_block=lambda: (
                    holder[0]._connectors.context_block() if holder else ""
                ),
            )

        transport = InProcessTransport()
        host = AldwinHost(transport, agent_builder=builder, workspace=workspace.root)
        holder.append(host)
        host._connectors._connection_factory = _factory(build_fixture_server())
        host._connectors._catalog = CATALOG
        await host.start()
        rig = Rig(host, AldwinClient(transport.connect()), fake_model, aldwin_home)
        await rig.client.connect()
        rigs.append(rig)
        return rig

    try:
        yield make
    finally:
        for rig in rigs:
            await rig.client.close()
            await rig.host.stop_host()
        stdio.kill_live_groups()


def _approvals(events: list[Event]) -> list[ApprovalEvent]:
    return [e for e in events if isinstance(e, ApprovalEvent)]


def _log_lines(home: Path) -> list[dict[str, Any]]:
    path = home / "logs" / "approvals.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


async def test_catalogue_app_connects_and_its_tools_load(make_rig: RigFactory) -> None:
    rig = await make_rig()
    rig.model.add_turns(
        tool_call("propose_connector", {"name": "plain"}), final("done")
    )
    events = await rig.turn("connect plain")

    [approval] = _approvals(events)
    assert approval.summary == "Aldwin wants to add the Plain connector"
    assert approval.detail == "https://plain.example/mcp"
    assert approval.risky == ["adds an external connector"]
    assert approval.grantable is False
    assert "connected: plain, 5 tools loaded" in rig.tool_text()
    loaded = [n for n in rig.host._agent_session.tools if n.startswith("mcp__plain__")]
    assert len(loaded) == 5
    assert "plain" in load_connectors(rig.home).entries


async def test_no_returns_the_denial_and_saves_nothing(make_rig: RigFactory) -> None:
    rig = await make_rig()
    rig.model.add_turns(tool_call("propose_connector", {"name": "plain"}), final("ok"))
    await rig.turn("connect plain", answer="no")

    assert DENIAL_TEXT["user"] in rig.tool_text()
    assert not connectors_path(rig.home).exists()
    line = _log_lines(rig.home)[-1]
    assert (line["tool"], line["answer"]) == ("propose_connector", "no")


async def test_already_connected_asks_nothing(make_rig: RigFactory) -> None:
    rig = await make_rig()
    save_connectors(
        rig.home,
        {"plain": ConnectorConfig(name="plain", url="https://plain.example/mcp")},
    )
    await rig.manager.reconcile()
    rig.model.add_turns(tool_call("propose_connector", {"name": "plain"}), final("ok"))
    events = await rig.turn("connect plain")

    assert _approvals(events) == []
    assert "already connected: plain" in rig.tool_text()
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.ok is False


async def test_token_is_asked_privately_during_the_turn(make_rig: RigFactory) -> None:
    rig = await make_rig()
    rig.model.add_turns(tool_call("propose_connector", {"name": "gated"}), final("ok"))
    seen: list[ConnectorAuthEvent] = []

    async def give_token(event: ConnectorAuthEvent) -> None:
        seen.append(event)
        await rig.client.connectors_auth_token(event.name, TOKEN)

    events = await rig.turn("connect gated", on_auth=give_token)

    [auth] = seen
    assert (auth.mode, auth.name, auth.token_help_url) == (
        "token",
        "gated",
        "https://gated.example/tokens",
    )
    assert auth.re == events[0].re
    assert "connected: gated, 5 tools loaded" in rig.tool_text()
    stored = FileCredentialStore(rig.home).get("gated")
    assert stored is not None and stored["token"] == TOKEN
    # The token reached the store and nothing else.
    everywhere = [
        connectors_path(rig.home).read_text("utf-8"),
        *(e.model_dump_json() for e in events),
        *(json.dumps(m.content) for m in rig.model.calls[-1]),
        *(p.read_text("utf-8") for p in (rig.home / "logs").glob("*")),
        *(p.read_text("utf-8") for p in (rig.home / "sessions").glob("*.json")),
    ]
    assert not any(TOKEN in text for text in everywhere)


async def test_stop_during_sign_in_saves_nothing(make_rig: RigFactory) -> None:
    rig = await make_rig()
    rig.model.add_turns(tool_call("propose_connector", {"name": "gated"}), final("ok"))

    async def give_up(event: ConnectorAuthEvent) -> None:
        await rig.client.stop()

    events = await rig.turn("connect gated", on_auth=give_up)

    done = [e for e in events if isinstance(e, DoneEvent)]
    assert done and done[-1].reason == "stopped"
    assert not connectors_path(rig.home).exists()
    assert FileCredentialStore(rig.home).get("gated") is None


async def test_custom_url_starts_cautious(make_rig: RigFactory) -> None:
    rig = await make_rig()
    url = "https://custom.example/mcp"
    rig.model.add_turns(tool_call("propose_connector", {"url": url}), final("ok"))
    events = await rig.turn("connect my server")

    [approval] = _approvals(events)
    assert approval.summary == "Aldwin wants to add a connector: custom"
    assert approval.detail == f"{url}\nIts tools will ask before running."
    assert approval.risky == [
        "adds an external connector",
        "custom server, not in the catalogue",
    ]
    saved = load_connectors(rig.home).entries["custom"]
    assert saved.trust_read_only_hints is False
    custom = next(s for s in rig.manager.snapshots() if s.name == "custom")
    assert all(t.effective == "ask" for t in custom.tools)


async def test_local_command_is_asked_once(make_rig: RigFactory) -> None:
    rig = await make_rig()
    command = shlex.join(fixture_argv())
    rig.model.add_turns(
        tool_call("propose_connector", {"command": command}), final("ok")
    )
    events = await rig.turn("run my fixture server")

    [approval] = _approvals(events)
    assert approval.summary.startswith("Aldwin wants to run a local connector: ")
    assert approval.detail is not None and approval.detail.startswith("Command: ")
    assert "runs a program on this computer" in approval.risky
    assert rig.tool_text().count("connected: ") == 1
    tools = [line["tool"] for line in _log_lines(rig.home)[-2:]]
    assert tools == ["propose_connector", "connectors.add"]
    name = next(iter(load_connectors(rig.home).entries))
    await rig.manager._stop_live(name)


async def test_another_proposal_asks_again(make_rig: RigFactory) -> None:
    rig = await make_rig()
    rig.model.add_turns(
        tool_call("propose_connector", {"url": "https://one.example/mcp"}),
        final("ok"),
        tool_call("propose_connector", {"url": "https://two.example/mcp"}),
        final("ok"),
    )
    first = await rig.turn("connect one")
    second = await rig.turn("connect two")
    assert len(_approvals(first)) == len(_approvals(second)) == 1


async def test_available_apps_are_offered_until_added(make_rig: RigFactory) -> None:
    rig = await make_rig()
    block = rig.manager.context_block()
    assert "Available to connect" in block and "plain, gated" in block
    rig.model.add_turns(tool_call("propose_connector", {"name": "plain"}), final("ok"))
    await rig.turn("connect plain")
    after = rig.manager.context_block()
    assert "- plain (Plain): connected" in after
    assert "call propose_connector): gated" in after


@pytest.mark.parametrize(
    ("error", "kind", "reason"),
    [
        (ConnectorNotApproved("Plain"), "changed", ""),
        (SignInCancelled(), "cancelled", ""),
        (SignInTimedOut(), "sign_in_failed", "timed out"),
        (
            NeedsSignIn("Plain needs you to sign in: /connectors."),
            "sign_in_failed",
            "Plain needs you to sign in: /connectors.",
        ),
        (ConnectorCommandNotFound("npx"), "failed", "command not found: npx"),
        (
            ConnectorUnreachable("plain.example", "down"),
            "failed",
            "Couldn't reach plain.example: down.",
        ),
    ],
    ids=[
        "changed",
        "cancelled",
        "timed-out",
        "needs-sign-in",
        "no-command",
        "unreachable",
    ],
)
async def test_each_failure_becomes_its_own_line(
    make_rig: RigFactory,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    kind: str,
    reason: str,
) -> None:
    rig = await make_rig()

    async def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(rig.manager, "add", fail)
    proposal = ConnectorProposal(
        "catalog", ConnectorConfig(name="plain", url="https://plain.example/mcp")
    )
    outcome = await rig.manager.propose(proposal, None)
    assert (outcome.kind, outcome.reason) == (kind, reason)


async def test_a_connector_that_ends_up_failed_is_reported(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_rig()
    config = ConnectorConfig(name="plain", url="https://plain.example/mcp")
    failed = rig.manager._snapshot_of(config, "failed", [], "server said no")

    async def add(*_args: Any, **_kwargs: Any) -> Any:
        return AddResult(config, failed)

    monkeypatch.setattr(rig.manager, "add", add)
    outcome = await rig.manager.propose(ConnectorProposal("catalog", config), None)
    assert (outcome.kind, outcome.reason) == ("failed", "server said no")


async def test_turning_on_a_signed_out_app_signs_in_during_the_turn(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_rig()
    config = ConnectorConfig(
        name="plain", url="https://plain.example/mcp", auth="oauth", enabled=False
    )
    calls: list[Any] = []

    async def enable(name: str, **_kwargs: Any) -> Any:
        return rig.manager._snapshot_of(config, "needs_auth", [])

    async def login(name: str, *, on_auth: Any, **_kwargs: Any) -> Any:
        calls.append((name, on_auth))
        return rig.manager._snapshot_of(config, "connected", [])

    monkeypatch.setattr(rig.manager, "enable", enable)
    monkeypatch.setattr(rig.manager, "login", login)
    outcome = await rig.manager.propose(ConnectorProposal("enable", config), None)
    assert outcome.kind == "connected"
    assert calls == [("plain", rig.manager._turn_auth)]


async def test_turning_on_an_imported_local_server_asks_once(
    make_rig: RigFactory, workspace: Workspace
) -> None:
    argv = fixture_argv()
    imported = ConnectorConfig(
        name="fixture",
        command=argv[0],
        args=argv[1:],
        enabled=False,
        note="review and enable",
    )
    rig = await make_rig()
    save_connectors(rig.home, {"fixture": imported})
    await rig.manager.reconcile()
    assert "Saved but turned off" in rig.manager.context_block()
    rig.model.add_turns(
        tool_call("propose_connector", {"name": "fixture"}), final("ok")
    )
    events = await rig.turn("turn on my fixture server")

    [approval] = _approvals(events)
    assert approval.summary == "Aldwin wants to turn on a local connector: fixture"
    assert approval.detail is not None and approval.detail.startswith("Command: ")
    assert "connected: fixture, " in rig.tool_text()
    tools = [line["tool"] for line in _log_lines(rig.home)[-2:]]
    assert tools == ["propose_connector", "connectors.enable"]
    saved = load_connectors(rig.home).entries["fixture"]
    assert saved.enabled and saved.note is None and saved.approved_digest is not None
    await rig.manager._stop_live("fixture")
