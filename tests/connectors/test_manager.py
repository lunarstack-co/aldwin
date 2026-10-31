"""`ConnectorManager` against the fixture server (`FR-10`, `FR-13` to `FR-18`)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from agentling import AgentSession
from fakes.model import FakeModel, final
from fixtures.mcp_server import (
    LONG_TOOL,
    FixtureControls,
    build_fixture_server,
    serve_http,
)

from aldwin.core.connectors.config import (
    ConnectorConfig,
    connectors_path,
    load_connectors,
    save_connectors,
)
from aldwin.core.connectors.connection import McpConnection
from aldwin.core.connectors.credentials import bearer_credential
from aldwin.core.connectors.errors import ConnectorConfigError, ConnectorUnreachable
from aldwin.core.connectors.naming import model_tool_name
from aldwin.core.policy import (
    REGISTRY,
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
)
from aldwin.core.workspace import Workspace
from aldwin.host.connectors import ConnectorAdmin, ConnectorEvent, ConnectorManager

FIXTURE_URL = "https://fixture.example/mcp"
FIXTURE_MODEL_NAMES = {
    "mcp__fixture__read_thing",
    "mcp__fixture__write_thing",
    "mcp__fixture__echo_nullable",
    "mcp__fixture__fail_thing",
    model_tool_name("fixture", LONG_TOOL),
}


class FakeSleep:
    """Records each backoff delay and waits until the test lets it pass."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self._gates: asyncio.Queue[asyncio.Future[None]] = asyncio.Queue()

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        gate: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        await self._gates.put(gate)
        await gate

    async def release(self) -> None:
        gate = await asyncio.wait_for(self._gates.get(), 5)
        gate.set_result(None)


class Recorder:
    def __init__(self) -> None:
        self.events: list[ConnectorEvent] = []
        self._changed = asyncio.Condition()

    async def __call__(self, event: ConnectorEvent) -> None:
        async with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    def statuses(self, name: str = "fixture") -> list[str]:
        return [
            e.snapshot.status
            for e in self.events
            if e.kind == "status" and e.snapshot and e.snapshot.name == name
        ]

    def notices(self) -> list[str]:
        return [e.message for e in self.events if e.kind == "notice"]

    async def wait_for(self, check: Callable[[], bool], timeout: float = 10) -> None:
        async with self._changed:
            await asyncio.wait_for(self._changed.wait_for(check), timeout)


async def _yes(_req: ApprovalRequest) -> ApprovalAnswer:
    return ApprovalAnswer("yes", "user")


def _in_process(*servers: tuple[str, FixtureControls]) -> Callable[..., McpConnection]:
    """A connection factory that routes each connector name to its fixture."""
    by_name = dict(servers)

    def factory(config: ConnectorConfig, **kwargs: Any) -> McpConnection:
        controls = by_name[config.name]
        return McpConnection(
            config, transport_factory=lambda _http: controls.server, **kwargs
        )

    return factory


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def policy_session(workspace: Workspace, aldwin_home: Path) -> PolicySession:
    return PolicySession(workspace=workspace, home=aldwin_home)


def _manager(
    home: Path,
    policy_session: PolicySession,
    recorder: Recorder,
    factory: Callable[..., McpConnection] | None = None,
    sleep: Any = None,
    environ: dict[str, str] | None = None,
) -> ConnectorManager:
    kwargs: dict[str, Any] = {}
    if factory is not None:
        kwargs["connection_factory"] = factory
    if sleep is not None:
        kwargs["sleep"] = sleep
    return ConnectorManager(
        admin=ConnectorAdmin(home, environ=environ or {}),
        policy_session=policy_session,
        ask=_yes,
        approval_log=None,
        emit=recorder,
        **kwargs,
    )


@pytest.fixture
async def manager(
    aldwin_home: Path,
    policy_session: PolicySession,
    recorder: Recorder,
    fixture_server: FixtureControls,
) -> AsyncIterator[ConnectorManager]:
    mgr = _manager(
        aldwin_home, policy_session, recorder, _in_process(("fixture", fixture_server))
    )
    await mgr.start()
    yield mgr
    await mgr.aclose()


def _names(specs: list[Any] | None) -> set[str]:
    out: set[str] = set()
    for spec in specs or []:
        fn = spec.get("function", spec)
        out.add(fn["name"])
    return out


async def _next_call_tools(fake_model: FakeModel, session: AgentSession) -> set[str]:
    fake_model.add_turns(final("ok"))
    async for _ev in session.run("hi", stream=True):
        pass
    return _names(fake_model.tool_specs[-1])


# --- add, attach, off -------------------------------------------------------


async def test_add_mid_conversation_adds_tools_before_next_call(
    manager: ConnectorManager,
    agent_session: AgentSession,
    fake_model: FakeModel,
    recorder: Recorder,
    aldwin_home: Path,
) -> None:
    """`TC-01`."""
    manager.attach(agent_session)
    result = await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    assert (result.tool_count, result.read, result.write) == (5, 1, 4)
    assert recorder.statuses() == ["connecting", "connected"]
    assert FIXTURE_MODEL_NAMES <= await _next_call_tools(fake_model, agent_session)
    assert "fixture" in load_connectors(aldwin_home).entries
    assert FIXTURE_MODEL_NAMES <= set(REGISTRY)


async def test_tool_set_off_is_absent_from_schemas(
    manager: ConnectorManager, agent_session: AgentSession, fake_model: FakeModel
) -> None:
    """`TC-05`."""
    manager.attach(agent_session)
    config = ConnectorConfig(
        name="fixture", url=FIXTURE_URL, tools={"write_thing": "off"}
    )
    await manager.add(config, token=None, dry_run=False)
    names = await _next_call_tools(fake_model, agent_session)
    assert "mcp__fixture__write_thing" not in names
    assert "mcp__fixture__read_thing" in names
    assert "mcp__fixture__write_thing" not in REGISTRY


async def test_dry_run_and_failures_save_nothing(
    manager: ConnectorManager, aldwin_home: Path
) -> None:
    probe = await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=True
    )
    assert probe.tool_count == 5
    assert not connectors_path(aldwin_home).exists()
    assert not any(n.startswith("mcp__") for n in REGISTRY)

    with pytest.raises(ConnectorConfigError):
        await manager.add(
            ConnectorConfig.model_construct(name="fixture", url="http://example.com"),
            token=None,
            dry_run=False,
        )
    assert not connectors_path(aldwin_home).exists()


async def test_settings_reapply_without_reconnecting(
    manager: ConnectorManager, agent_session: AgentSession, aldwin_home: Path
) -> None:
    manager.attach(agent_session)
    await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    live = manager._live["fixture"]
    connection = live.connection

    await manager.set_tool_setting("fixture", "write_thing", "off")
    assert "mcp__fixture__write_thing" not in agent_session.tools
    assert load_connectors(aldwin_home).entries["fixture"].tools == {
        "write_thing": "off"
    }
    await manager.set_tool_setting("fixture", "write_thing", "default")
    assert "mcp__fixture__write_thing" in agent_session.tools

    await manager.set_trust_hints("fixture", False)
    assert REGISTRY["mcp__fixture__read_thing"].tier == "ask"
    assert live.connection is connection


async def test_disable_enable_remove(
    manager: ConnectorManager,
    agent_session: AgentSession,
    recorder: Recorder,
    aldwin_home: Path,
) -> None:
    manager.attach(agent_session)
    await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    snap = await manager.disable("fixture")
    assert snap.status == "disabled"
    assert not any(n.startswith("mcp__") for n in agent_session.tools)
    assert not any(n.startswith("mcp__") for n in REGISTRY)

    snap = await manager.enable("fixture")
    assert snap.status == "connected"
    assert "mcp__fixture__read_thing" in agent_session.tools

    await manager.remove("fixture")
    assert load_connectors(aldwin_home).entries == {}
    assert not any(n.startswith("mcp__") for n in agent_session.tools)
    assert manager.snapshots() == []


async def test_attach_readds_tools_after_a_rebuild(
    manager: ConnectorManager, agent_session: AgentSession, fake_model: FakeModel
) -> None:
    """`FR-21`: a fresh session gets every connector tool back."""
    await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    manager.attach(agent_session)
    assert FIXTURE_MODEL_NAMES <= await _next_call_tools(fake_model, agent_session)


async def test_long_name_is_stable_across_managers(
    aldwin_home: Path,
    policy_session: PolicySession,
    recorder: Recorder,
    fixture_server: FixtureControls,
) -> None:
    """`TC-06`: the same 64-character name after a restart."""
    save_connectors(
        aldwin_home, {"fixture": ConnectorConfig(name="fixture", url=FIXTURE_URL)}
    )
    seen: list[set[str]] = []
    for _ in range(2):
        mgr = _manager(
            aldwin_home,
            policy_session,
            recorder,
            _in_process(("fixture", build_fixture_server())),
        )
        await mgr.start()
        await recorder.wait_for(lambda: "connected" in recorder.statuses())
        seen.append(set(mgr._live["fixture"].tools))
        await mgr.aclose()
        recorder.events.clear()
    assert seen[0] == seen[1]
    assert model_tool_name("fixture", LONG_TOOL) in seen[0]


# --- start, close -----------------------------------------------------------


async def test_start_returns_at_once_with_slow_connectors(
    aldwin_home: Path, policy_session: PolicySession, recorder: Recorder
) -> None:
    servers = []
    for name in ("a", "b", "c"):
        controls = build_fixture_server()
        controls.delay_connect(1.0)
        servers.append((name, controls))
    save_connectors(
        aldwin_home,
        {
            n: ConnectorConfig(name=n, url=f"https://{n}.example/mcp")
            for n, _ in servers
        },
    )
    mgr = _manager(aldwin_home, policy_session, recorder, _in_process(*servers))
    started = time.perf_counter()
    await mgr.start()
    assert time.perf_counter() - started < 0.05
    await mgr.aclose()


async def test_aclose_returns_while_a_call_hangs(
    manager: ConnectorManager, fixture_server: FixtureControls
) -> None:
    await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    fixture_server.hang_tool("read_thing")
    connection = manager._live["fixture"].connection
    assert connection is not None
    call = asyncio.create_task(connection.call_tool("read_thing", {}))
    await asyncio.sleep(0.05)
    started = time.perf_counter()
    await manager.aclose(timeout=0.5)
    assert time.perf_counter() - started < 3
    call.cancel()
    await asyncio.gather(call, return_exceptions=True)


async def test_invalid_file_starts_with_connectors_off(
    aldwin_home: Path, policy_session: PolicySession, recorder: Recorder
) -> None:
    connectors_path(aldwin_home).write_text(
        "connectors:\n  x:\n    url: https://x.example/mcp\n    enabled: maybe\n",
        encoding="utf-8",
    )
    mgr = _manager(aldwin_home, policy_session, recorder)
    await mgr.start()
    assert mgr.load_error == "connectors.yaml: connectors.x.enabled must be a boolean"
    assert mgr.snapshots() == []
    await mgr.aclose()


# --- list_changed, reconnect ------------------------------------------------


async def test_list_changed_updates_session_and_registry(
    manager: ConnectorManager,
    agent_session: AgentSession,
    fixture_server: FixtureControls,
) -> None:
    """`TC-08`."""
    manager.attach(agent_session)
    await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    await fixture_server.swap_tools()

    async def _swapped() -> None:
        while "mcp__fixture__new_thing" not in agent_session.tools:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_swapped(), 5)
    assert "mcp__fixture__write_thing" not in agent_session.tools
    assert "mcp__fixture__new_thing" in REGISTRY
    assert "mcp__fixture__write_thing" not in REGISTRY


async def test_running_call_finishes_when_its_tool_is_removed(
    manager: ConnectorManager,
    agent_session: AgentSession,
    fixture_server: FixtureControls,
) -> None:
    manager.attach(agent_session)
    await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    fixture_server.slow_tool("write_thing", 0.3)
    tool = agent_session.tools["mcp__fixture__write_thing"]
    call = asyncio.create_task(tool.call({"value": "x"}))
    await asyncio.sleep(0.05)
    await fixture_server.swap_tools()
    out = await asyncio.wait_for(call, 5)
    assert "wrote x" in out


@pytest.mark.loopback
async def test_server_restart_reconnects_with_backoff(
    aldwin_home: Path,
    policy_session: PolicySession,
    recorder: Recorder,
    agent_session: AgentSession,
) -> None:
    """`TC-09`."""
    controls = build_fixture_server()
    sleep = FakeSleep()
    async with serve_http(controls) as url:
        port = int(url.rsplit(":", 1)[1])
        save_connectors(
            aldwin_home,
            {"fixture": ConnectorConfig(name="fixture", url=f"{url}/mcp")},
        )
        mgr = _manager(aldwin_home, policy_session, recorder, sleep=sleep)
        mgr.attach(agent_session)
        await mgr.start()
        await recorder.wait_for(lambda: "connected" in recorder.statuses())
        assert "mcp__fixture__read_thing" in agent_session.tools

    await recorder.wait_for(
        lambda: "Fixture disconnected; retrying." in recorder.notices()
    )
    assert recorder.statuses()[-1] == "failed"
    assert not any(n.startswith("mcp__") for n in agent_session.tools)
    assert mgr.snapshots()[0].retry_in_s == 30

    await sleep.release()  # server still down: this attempt fails
    await recorder.wait_for(
        lambda: recorder.statuses()[-1] == "failed" and len(sleep.delays) == 2
    )
    assert mgr.snapshots()[0].retry_in_s == 60

    async with serve_http(controls, port=port):
        await sleep.release()
        await recorder.wait_for(lambda: "Fixture reconnected." in recorder.notices())
        assert "mcp__fixture__read_thing" in agent_session.tools
        assert sleep.delays == [30.0, 60.0]
        await mgr.aclose()


# --- cross-process ----------------------------------------------------------


async def test_second_process_picks_up_new_connector(
    aldwin_home: Path, workspace: Workspace
) -> None:
    """`TC-11`: B starts its next turn and sees what A added; B keeps the rest."""
    fixture, other = build_fixture_server(), build_fixture_server()
    factory = _in_process(("fixture", fixture), ("other", other))
    save_connectors(
        aldwin_home,
        {"other": ConnectorConfig(name="other", url="https://other.example/mcp")},
    )
    rec_a, rec_b = Recorder(), Recorder()
    a = _manager(
        aldwin_home,
        PolicySession(workspace=workspace, home=aldwin_home),
        rec_a,
        factory,
    )
    b = _manager(
        aldwin_home,
        PolicySession(workspace=workspace, home=aldwin_home),
        rec_b,
        factory,
    )
    await a.start()
    await b.start()
    await rec_b.wait_for(lambda: "connected" in rec_b.statuses("other"))
    b_other = b._live["other"].connection

    await a.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    await b.reconcile()
    await rec_b.wait_for(lambda: "connected" in rec_b.statuses("fixture"))
    assert b._live["other"].connection is b_other
    await a.aclose()
    await b.aclose()


async def test_reconcile_restarts_only_on_a_new_token(
    aldwin_home: Path, policy_session: PolicySession, recorder: Recorder
) -> None:
    fixture = build_fixture_server()
    config = ConnectorConfig(name="fixture", url=FIXTURE_URL, auth="bearer")
    save_connectors(aldwin_home, {"fixture": config})
    mgr = _manager(
        aldwin_home, policy_session, recorder, _in_process(("fixture", fixture))
    )
    mgr.admin.store.set("fixture", bearer_credential("one"))
    await mgr.start()
    await recorder.wait_for(lambda: "connected" in recorder.statuses())
    first = mgr._live["fixture"].connection

    await asyncio.sleep(0.01)
    mgr.admin.store.set("fixture", bearer_credential("one"))
    (aldwin_home / "credentials" / "mcp" / "fixture.lock").touch()
    await mgr.reconcile()
    assert mgr._live["fixture"].connection is first

    mgr.admin.store.set("fixture", bearer_credential("two"))
    await mgr.reconcile()
    await recorder.wait_for(lambda: recorder.statuses().count("connected") == 2)
    assert mgr._live["fixture"].connection is not first
    await mgr.aclose()


async def test_unreachable_connector_reports_failed_and_retries(
    aldwin_home: Path, policy_session: PolicySession, recorder: Recorder
) -> None:
    def factory(config: ConnectorConfig, **kwargs: Any) -> McpConnection:
        class _Down(McpConnection):
            async def open(self, *, timeout: float = 30.0) -> Any:
                raise ConnectorUnreachable("down.example", "connection refused")

        return _Down(config, **kwargs)

    sleep = FakeSleep()
    save_connectors(
        aldwin_home,
        {"down": ConnectorConfig(name="down", url="https://down.example/mcp")},
    )
    mgr = _manager(aldwin_home, policy_session, recorder, factory, sleep=sleep)
    await mgr.start()
    await recorder.wait_for(lambda: "failed" in recorder.statuses("down"))
    snap = mgr.snapshots()[0]
    assert snap.error == "Couldn't reach down.example: connection refused."
    assert snap.retry_in_s == 30
    assert "needs attention" in mgr.context_block()
    await mgr.aclose()


async def test_relist_after_stop_does_not_bring_tools_back(
    manager: ConnectorManager,
    agent_session: AgentSession,
    fixture_server: FixtureControls,
) -> None:
    manager.attach(agent_session)
    await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    fixture_server.delay_connect(0.3)  # slows the re-list `list_changed` triggers
    await fixture_server.swap_tools()
    await asyncio.sleep(0.05)
    await manager.disable("fixture")
    await asyncio.sleep(0.5)
    assert not any(n.startswith("mcp__") for n in REGISTRY)
    assert not any(n.startswith("mcp__") for n in agent_session.tools)


# --- races and failure paths -------------------------------------------------


@pytest.mark.parametrize("stop", ["disable", "remove"])
async def test_stop_during_enable_leaves_nothing_live(
    aldwin_home: Path,
    policy_session: PolicySession,
    recorder: Recorder,
    agent_session: AgentSession,
    stop: str,
) -> None:
    """A connector stopped while it is still connecting never brings its tools back."""
    fixture = build_fixture_server()
    fixture.delay_connect(0.3)
    save_connectors(
        aldwin_home,
        {"fixture": ConnectorConfig(name="fixture", url=FIXTURE_URL, enabled=False)},
    )
    mgr = _manager(
        aldwin_home, policy_session, recorder, _in_process(("fixture", fixture))
    )
    await mgr.start()
    mgr.attach(agent_session)
    enabling = asyncio.create_task(mgr.enable("fixture"))
    await asyncio.sleep(0.05)
    await getattr(mgr, stop)("fixture")
    await enabling
    await asyncio.sleep(0.05)
    assert mgr._live == {}
    assert not any(n.startswith("mcp__") for n in agent_session.tools)
    assert not any(n.startswith("mcp__") for n in REGISTRY)
    await mgr.aclose()


async def test_supervisor_failure_is_logged_and_shown(
    aldwin_home: Path,
    policy_session: PolicySession,
    recorder: Recorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(config: ConnectorConfig, **kwargs: Any) -> McpConnection:
        class _Broken(McpConnection):
            async def open(self, *, timeout: float = 30.0) -> Any:
                raise RuntimeError("unexpected")

        return _Broken(config, **kwargs)

    save_connectors(
        aldwin_home, {"fixture": ConnectorConfig(name="fixture", url=FIXTURE_URL)}
    )
    mgr = _manager(aldwin_home, policy_session, recorder, broken)
    await mgr.start()
    supervisor = mgr._live["fixture"].supervisor
    assert supervisor is not None
    await asyncio.gather(supervisor, return_exceptions=True)
    await asyncio.sleep(0)
    assert "connector fixture stopped retrying" in caplog.text
    snap = mgr.snapshots()[0]
    assert (snap.status, snap.error) == (
        "failed",
        "stopped unexpectedly; see aldwin.log",
    )
    await mgr.aclose()


async def test_backoff_doubles_and_caps_at_ten_minutes(
    aldwin_home: Path, policy_session: PolicySession, recorder: Recorder
) -> None:
    def down(config: ConnectorConfig, **kwargs: Any) -> McpConnection:
        class _Down(McpConnection):
            async def open(self, *, timeout: float = 30.0) -> Any:
                raise ConnectorUnreachable("down.example", "refused")

        return _Down(config, **kwargs)

    sleep = FakeSleep()
    save_connectors(
        aldwin_home,
        {"down": ConnectorConfig(name="down", url="https://down.example/mcp")},
    )
    mgr = _manager(aldwin_home, policy_session, recorder, down, sleep=sleep)
    await mgr.start()
    for _ in range(6):
        await sleep.release()
    await recorder.wait_for(lambda: len(sleep.delays) == 7)
    assert sleep.delays == [30.0, 60.0, 120.0, 240.0, 480.0, 600.0, 600.0]
    await mgr.aclose()


async def test_name_collision_second_skipped(
    manager: ConnectorManager,
    agent_session: AgentSession,
    fixture_server: FixtureControls,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`a.b` and `a_b` both become `mcp__fixture__a_b`; the second is skipped."""

    async def first() -> str:
        return "first"

    async def second() -> str:
        return "second"

    fixture_server.server.add_tool(first, name="dup.thing", structured_output=False)
    fixture_server.server.add_tool(second, name="dup_thing", structured_output=False)
    manager.attach(agent_session)
    await manager.add(
        ConnectorConfig(name="fixture", url=FIXTURE_URL), token=None, dry_run=False
    )
    assert "mcp__fixture__dup_thing" in agent_session.tools
    assert "tools collide on mcp__fixture__dup_thing" in caplog.text
