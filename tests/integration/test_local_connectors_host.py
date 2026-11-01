"""Local (stdio) connectors end to end: host, client, approval, fixture server."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from conftest import collect
from connectors._proc import fixture_argv, wait_until_gone, wait_until_gone_async
from fakes.model import FakeModel, final, tool_call
from fixtures.mcp_server import FixtureControls, build_fixture_server

from aldwin.client import AldwinClient, ProtocolError
from aldwin.core.agent import build_agent
from aldwin.core.connectors import stdio
from aldwin.core.connectors.config import (
    ConnectorConfig,
    connectors_path,
    load_connectors,
    save_connectors,
)
from aldwin.core.connectors.connection import McpConnection
from aldwin.core.connectors.credentials import FileCredentialStore
from aldwin.core.connectors.errors import ConnectorError, ConnectorNotApproved
from aldwin.core.connectors.local import RUNS_A_PROGRAM, launch_digest
from aldwin.core.connectors.stderr_log import ServerLog
from aldwin.core.policy import ApprovalAnswer, ApprovalRequest
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.workspace import Workspace
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.host.connectors import NEEDS_APPROVAL, SSE_UNSUPPORTED, WINDOWS_UNSUPPORTED
from aldwin.protocol import (
    ApprovalEvent,
    ConnectorProbeEvent,
    ConnectorStatusEvent,
    DoneEvent,
    Event,
    ToolResultEvent,
)

ARGV = fixture_argv()


def _local(
    name: str = "fixture", *, approved: bool = True, **overrides: Any
) -> ConnectorConfig:
    config = ConnectorConfig.model_validate(
        {"name": name, "command": ARGV[0], "args": ARGV[1:], **overrides}
    )
    if approved:
        config = config.model_copy(update={"approved_digest": launch_digest(config)})
    return config


@dataclass
class Rig:
    host: AldwinHost
    client: AldwinClient
    model: FakeModel
    home: Path
    workspace: Workspace
    sent: list[Event] = field(default_factory=list)

    @property
    def manager(self) -> Any:
        return self.host._connectors

    def status(self, name: str = "fixture") -> Any:
        return next(s for s in self.manager.snapshots() if s.name == name)

    async def wait_status(self, status: str, name: str = "fixture") -> Any:
        async with asyncio.timeout(15):
            while (snap := self.status(name)).status != status:
                await asyncio.sleep(0.05)
        return snap

    async def call(self, tool: str, **args: Any) -> Any:
        """Call a fixture tool through `McpTool.forward`, skipping the approval."""
        mgr = self.manager
        found = mgr._index.get("fixture", tool)
        assert found is not None, f"{tool} not indexed"
        mgr.load([found])
        wrapped = self.host._agent_session.tools[f"mcp__fixture__{tool}"]
        text = await wrapped.inner_tool.forward(**args)  # type: ignore[attr-defined]
        try:
            return json.loads(text)
        except ValueError:
            return text


def _neighbour(controls: FixtureControls) -> Any:
    """Remote entries use the in-process fixture; local ones the real transport."""

    def factory(config: ConnectorConfig, **kwargs: Any) -> McpConnection:
        if config.kind == "local":
            return McpConnection(config, **kwargs)
        return McpConnection(
            config, transport_factory=lambda _http: controls.server, **kwargs
        )

    return factory


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
        host._connectors._connection_factory = _neighbour(build_fixture_server())
        rig = Rig(
            host, AldwinClient(transport.connect()), fake_model, aldwin_home, workspace
        )
        original = transport.send_event

        async def record(event: Event) -> None:
            rig.sent.append(event)
            await original(event)

        transport.send_event = record  # type: ignore[method-assign]
        host._approvals._send_event = record
        await host.start()
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


@pytest.fixture(autouse=True)
def live_groups_cleanup() -> Any:
    try:
        yield
    finally:
        leaked = set(stdio._LIVE_PGIDS)
        stdio.kill_live_groups()
        assert not leaked, f"process groups left running: {sorted(leaked)}"


def _answer(value: str, calls: list[ApprovalEvent] | None = None) -> Any:
    async def handler(event: ApprovalEvent) -> tuple[Any, str | None]:
        if calls is not None:
            calls.append(event)
        return value, None

    return handler


def _log_lines(home: Path) -> list[dict[str, Any]]:
    path = home / "logs" / "approvals.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


async def _started(rig: Rig, *configs: ConnectorConfig) -> None:
    save_connectors(rig.home, {c.name: c for c in configs})
    await rig.manager.reconcile()


# --- environment, folder, logs ---------------------------------------------------


async def test_env_tool_has_no_unlisted_secrets(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("MY_TOKEN", "y")
    monkeypatch.setenv("GITHUB_TOKEN", "z")
    rig = await make_rig()
    await _started(rig, _local())
    await rig.wait_status("connected")
    env = await rig.call("env")
    assert not {"OPENAI_API_KEY", "MY_TOKEN", "GITHUB_TOKEN"} & set(env)
    assert env["PATH"] and env["HOME"] == os.environ["HOME"]


async def test_env_tool_includes_allowlisted_token(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("MY_TOKEN", "y")
    rig = await make_rig()
    await _started(rig, _local(env_allow=["MY_TOKEN"]))
    await rig.wait_status("connected")
    env = await rig.call("env")
    assert env["MY_TOKEN"] == "y" and "OPENAI_API_KEY" not in env


async def test_stored_env_values_reach_child(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch, aldwin_home: Path
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "fromenv")
    FileCredentialStore(aldwin_home).set(
        "fixture",
        {
            "v": 1,
            "kind": "env",
            "env": {
                "GITHUB_TOKEN": "stored",
                "DATABASE_URL": "postgres://u:fakepass@localhost/db",
            },
        },
    )
    rig = await make_rig()
    await _started(rig, _local(env_allow=["GITHUB_TOKEN", "DATABASE_URL"]))
    await rig.wait_status("connected")
    env = await rig.call("env")
    assert env["GITHUB_TOKEN"] == "stored"
    assert env["DATABASE_URL"] == "postgres://u:fakepass@localhost/db"


async def test_cwd_tool_returns_workspace_root(
    make_rig: RigFactory, workspace: Workspace
) -> None:
    rig = await make_rig()
    await _started(rig, _local())
    await rig.wait_status("connected")
    assert Path(await rig.call("cwd")).resolve() == workspace.root.resolve()


async def test_stderr_goes_to_private_log_not_events(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    rig = await make_rig()
    await _started(rig, _local())
    await rig.wait_status("connected")
    await rig.manager.set_tool_setting("fixture", "say_stderr", "auto")
    rig.model.add_turns(
        tool_call(
            "find_connector_tools", {"connector": "fixture", "names": ["say_stderr"]}
        ),
        tool_call("mcp__fixture__say_stderr", {"text": "fixture-stderr-4b1e"}),
        final("done"),
    )
    events = await collect(rig.client.turn("write to stderr"))
    log = aldwin_home / "logs" / "mcp-fixture.log"
    assert log.read_text("utf-8").splitlines()[-1].endswith("fixture-stderr-4b1e")
    assert os.stat(log).st_mode & 0o777 == 0o600
    # The call's own arguments are shown in `tool_call`; the server's stderr must
    # never come back as a result.
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert results and all(
        "fixture-stderr-4b1e" not in r.model_dump_json() for r in results
    )
    assert [r.ok for r in results] == [True, True]


# --- lifecycle --------------------------------------------------------------------


async def test_stop_host_kills_server_group_within_5s(make_rig: RigFactory) -> None:
    rig = await make_rig()
    await _started(rig, _local())
    await rig.wait_status("connected")
    pids = await rig.call("pids")
    await rig.client.close()
    await rig.host.stop_host()
    assert wait_until_gone([pids["pid"], pids["child"]], 5.0) < 5.0
    assert not stdio._LIVE_PGIDS


async def test_crash_marks_failed_then_reconnects(make_rig: RigFactory) -> None:
    rig = await make_rig()
    delays: list[float] = []

    async def fast_sleep(delay: float) -> None:
        delays.append(delay)
        await asyncio.sleep(0)

    rig.manager._sleep = fast_sleep
    statuses: list[tuple[str, int | None]] = []
    emit = rig.manager._emit

    async def record(event: Any) -> None:
        if event.kind == "status" and event.snapshot.name == "fixture":
            statuses.append((event.snapshot.status, event.snapshot.retry_in_s))
        await emit(event)

    rig.manager._emit = record
    await _started(rig, _local())
    await rig.wait_status("connected")
    old = await rig.call("pids")
    os.kill(old["pid"], signal.SIGKILL)
    async with asyncio.timeout(15):
        while ("failed", 30) not in statuses:
            await asyncio.sleep(0.05)
    await rig.wait_status("connected")
    assert 30 in delays
    new = await rig.call("pids")
    assert new["pid"] != old["pid"]
    wait_until_gone([old["child"]], 5.0)


async def test_missing_command_fails_alone(make_rig: RigFactory) -> None:
    rig = await make_rig()
    missing = _local("missing", command="does-not-exist", args=[])
    remote = ConnectorConfig(name="fixture-http", url="https://fixture.example/mcp")
    await _started(rig, missing, remote)
    snap = await rig.wait_status("failed", "missing")
    assert snap.error == "command not found: does-not-exist"
    remote_snap = await rig.wait_status("connected", "fixture-http")
    assert len(remote_snap.tools) == 5
    assert not stdio._LIVE_PGIDS


@pytest.mark.parametrize("action", ["disable", "remove", "reconnect"])
async def test_stop_paths_kill_group(
    make_rig: RigFactory, action: str, aldwin_home: Path
) -> None:
    rig = await make_rig()
    await _started(rig, _local())
    await rig.wait_status("connected")
    old = await rig.call("pids")
    await getattr(rig.manager, action)("fixture")
    wait_until_gone([old["pid"], old["child"]], 5.0)
    if action == "reconnect":
        await rig.wait_status("connected")
        assert (await rig.call("pids"))["pid"] != old["pid"]
    if action == "remove":
        assert "fixture" not in load_connectors(aldwin_home).entries


async def test_abandoned_close_kills_live_groups(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_rig()
    await _started(rig, _local())
    await rig.wait_status("connected")
    pids = await rig.call("pids")

    async def hang(self: Any, *exc: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(stdio.StdioTransport, "__aexit__", hang)
    loop = asyncio.get_running_loop()
    started = loop.time()
    await rig.manager.aclose(timeout=0.5)
    assert loop.time() - started < 1.5
    wait_until_gone([pids["pid"], pids["child"]], 2.0)
    assert not stdio._LIVE_PGIDS


async def test_initialize_timeout_fails_and_kills_group(
    workspace: Workspace, aldwin_home: Path
) -> None:
    from aldwin.host.connectors import ConnectorAdmin

    config = _local(args=[*ARGV[1:], "--silent"])
    spec = ConnectorAdmin(aldwin_home).launch_spec(
        config, workspace_root=workspace.root
    )
    made: list[stdio.StdioTransport] = []

    def transport(_http: Any) -> stdio.StdioTransport:
        made.append(stdio.StdioTransport(spec, log=ServerLog(aldwin_home, "fixture")))
        return made[0]

    async def ignore(*_args: Any) -> None:
        return None

    connection = McpConnection(
        config,
        auth=None,
        on_tools_changed=ignore,
        on_lost=ignore,
        transport_factory=transport,
    )
    with pytest.raises(ConnectorError) as info:
        await connection.open(timeout=0.5)
    assert info.value.message == "Fixture didn't start within 0.5s."
    assert made and made[0].pid is not None
    await wait_until_gone_async([made[0].pid], 2.0)
    # The stop finishes in the abandoned task: the whole group, then the registry.
    async with asyncio.timeout(2.0):
        while stdio._LIVE_PGIDS:
            await asyncio.sleep(0.05)


async def test_workspace_change_keeps_running_server(
    make_rig: RigFactory, workspace: Workspace, tmp_path: Path
) -> None:
    rig = await make_rig()
    config = _local()
    await _started(rig, config)
    await rig.wait_status("connected")
    pid = (await rig.call("pids"))["pid"]
    second = tmp_path / "ws2"
    second.mkdir()
    rig.host.set_workspace(second)
    assert (await rig.call("pids"))["pid"] == pid
    assert Path(await rig.call("cwd")).resolve() == workspace.root.resolve()
    assert launch_digest(config) == config.approved_digest
    await rig.manager.reconnect("fixture")
    await rig.wait_status("connected")
    assert Path(await rig.call("cwd")).resolve() == second.resolve()
    assert not any(isinstance(e, ApprovalEvent) for e in rig.sent)


async def test_local_entry_fails_on_windows(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aldwin.host.connectors as host_connectors

    monkeypatch.setattr(host_connectors, "_local_servers_supported", lambda: False)
    rig = await make_rig()
    await _started(rig, _local())
    snap = await rig.wait_status("failed")
    assert snap.error == WINDOWS_UNSUPPORTED
    assert not stdio._LIVE_PGIDS


async def test_sse_entry_never_connects(make_rig: RigFactory) -> None:
    rig = await make_rig()
    old = ConnectorConfig(name="old", url="https://example.com/sse", transport="sse")
    await _started(rig, old)
    snap = await rig.wait_status("failed", "old")
    assert snap.error == SSE_UNSUPPORTED
    assert rig.manager._live["old"].connection is None


async def test_digest_mismatch_blocks_spawn(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    rig = await make_rig()
    approved = _local()
    edited = approved.model_copy(update={"args": ["-S", *ARGV[2:]]})
    unapproved = _local("other", approved=False)
    await _started(rig, edited, unapproved)
    for name in ("fixture", "other"):
        snap = await rig.wait_status("failed", name)
        assert snap.error == NEEDS_APPROVAL
    assert not stdio._LIVE_PGIDS
    # Changing only `enabled` or `note` keeps the approval.
    noted = approved.model_copy(update={"note": "hello"})
    assert launch_digest(noted) == approved.approved_digest


# --- approval -----------------------------------------------------------------------


def _fixture_command() -> str:
    import shlex

    return shlex.join(ARGV)


async def test_chat_add_asks_on_request_re_in_order(make_rig: RigFactory) -> None:
    rig = await make_rig()
    calls: list[ApprovalEvent] = []
    status = await rig.client.connectors_add(
        command=_fixture_command(), name="fixture", on_approval=_answer("yes", calls)
    )
    assert isinstance(status, ConnectorStatusEvent) and status.status == "connected"
    [approval] = calls
    flow = [e.kind for e in rig.sent if getattr(e, "re", None) == approval.re]
    assert flow == ["approval", "connector.status", "done"]
    assert approval.tool == "connectors.add"
    assert approval.summary == "Run a local connector: fixture"
    assert approval.grantable is False and RUNS_A_PROGRAM in approval.risky
    assert approval.detail is not None
    for prefix in ("Command: ", "Folder: ", "Environment: "):
        assert prefix in approval.detail


async def test_approval_no_saves_nothing(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    rig = await make_rig()
    before = (
        connectors_path(aldwin_home).read_bytes()
        if connectors_path(aldwin_home).exists()
        else None
    )
    seen: list[set[int]] = []

    async def no(event: ApprovalEvent) -> tuple[Any, str | None]:
        seen.append(set(stdio._LIVE_PGIDS))
        return "no", None

    with pytest.raises(ProtocolError) as info:
        await rig.client.connectors_add(
            command=_fixture_command(), name="fixture", on_approval=no
        )
    assert (info.value.code, str(info.value)) == (
        "connector",
        "Fixture was not approved, so nothing was saved.",
    )
    after = (
        connectors_path(aldwin_home).read_bytes()
        if connectors_path(aldwin_home).exists()
        else None
    )
    assert after == before
    assert seen == [set()] and not stdio._LIVE_PGIDS
    assert _log_lines(aldwin_home)[-1]["answer"] == "no"


async def test_approval_yes_saves_digest_and_logs_labels(
    make_rig: RigFactory, aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_TOKEN", "secret-val-91")
    rig = await make_rig()
    asked: list[ApprovalEvent] = []
    await rig.client.connectors_add(
        command=_fixture_command(),
        name="fixture",
        env_allow=["MY_TOKEN"],
        on_approval=_answer("yes", asked),
    )
    saved = load_connectors(aldwin_home).entries["fixture"]
    assert saved.approved_digest == launch_digest(saved)
    line = _log_lines(aldwin_home)[-1]
    assert (line["tool"], line["connector"]) == ("connectors.add", "fixture")
    assert line["call_id"] == asked[0].approval_id
    assert line["labels"][0] == RUNS_A_PROGRAM
    assert "secret-val-91" not in (aldwin_home / "logs" / "approvals.jsonl").read_text()


async def test_connectors_add_without_handler_answers_no(make_rig: RigFactory) -> None:
    rig = await make_rig()
    with pytest.raises(ProtocolError) as info:
        await rig.client.connectors_add(command=_fixture_command(), name="fixture")
    assert "was not approved" in str(info.value)
    answers = [e for e in rig.sent if isinstance(e, DoneEvent) and e.re != "host"]
    assert answers and not stdio._LIVE_PGIDS


async def test_no_approver_denies(make_rig: RigFactory, aldwin_home: Path) -> None:
    rig = await make_rig()
    with pytest.raises(ConnectorNotApproved):
        await rig.manager.add(
            _local(approved=False), token=None, dry_run=False, approve=None
        )
    line = _log_lines(aldwin_home)[-1]
    assert line["source"] == "no_approver"
    assert "fixture" not in load_connectors(aldwin_home).entries


async def test_local_dry_run_does_not_spawn_or_ask(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    rig = await make_rig()
    calls: list[ApprovalEvent] = []
    probe = await rig.client.connectors_add(
        command="npx -y @modelcontextprotocol/server-everything",
        dry_run=True,
        on_approval=_answer("yes", calls),
    )
    assert isinstance(probe, ConnectorProbeEvent)
    assert (probe.name, probe.tools) == ("everything", [])
    assert calls == [] and not stdio._LIVE_PGIDS
    assert not connectors_path(aldwin_home).exists()


async def test_command_floor_refuses_before_asking(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    rig = await make_rig()
    calls: list[ApprovalEvent] = []
    for command, prefix in (
        ("rm -rf ~", "blocked command: "),
        ("cat ~/.ssh/id_rsa", "blocked: "),
    ):
        with pytest.raises(ProtocolError) as info:
            await rig.client.connectors_add(
                command=command, name="bad", on_approval=_answer("yes", calls)
            )
        assert info.value.code == "connector" and str(info.value).startswith(prefix)
    assert calls == [] and not stdio._LIVE_PGIDS
    assert not connectors_path(aldwin_home).exists()
    assert {line["decision"] for line in _log_lines(aldwin_home)} == {"blocked"}


async def test_add_with_url_and_command_is_error(make_rig: RigFactory) -> None:
    rig = await make_rig()
    with pytest.raises(ProtocolError) as info:
        await rig.client.connectors_add(
            url="https://a.example/mcp", command="npx pkg", name="x"
        )
    assert info.value.code == "connector"
    assert str(info.value) == "Give either a URL or a command, not both."
    with pytest.raises(ProtocolError) as info:
        await rig.client.connectors_add(name="x")
    assert str(info.value) == "Give a URL or a command."


async def test_enable_imported_asks_and_logs_enable(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    FileCredentialStore(aldwin_home).set(
        "github", {"v": 1, "kind": "env", "env": {"GITHUB_TOKEN": "ghp_fake0001"}}
    )
    imported = _local(
        "github",
        approved=False,
        enabled=False,
        env_allow=["GITHUB_TOKEN"],
        note="review and enable",
    )
    save_connectors(aldwin_home, {"github": imported})
    rig = await make_rig()
    seen: list[ApprovalEvent] = []
    with pytest.raises(ProtocolError):
        await rig.client.connectors_enable("github", on_approval=_answer("no", seen))
    assert load_connectors(aldwin_home).entries["github"].enabled is False
    assert not stdio._LIVE_PGIDS

    status = await rig.client.connectors_enable(
        "github", on_approval=_answer("yes", seen)
    )
    assert status.status == "connected"
    saved = load_connectors(aldwin_home).entries["github"]
    assert saved.enabled and saved.approved_digest == launch_digest(saved)
    line = _log_lines(aldwin_home)[-1]
    assert (line["tool"], line["connector"]) == ("connectors.enable", "github")
    assert seen[0].detail is not None and "GITHUB_TOKEN (saved)" in seen[0].detail
    assert "ghp_fake0001" not in seen[0].detail


@pytest.mark.parametrize("path", ["add", "enable"])
async def test_declined_approval_raises_not_approved(
    make_rig: RigFactory, aldwin_home: Path, path: str
) -> None:
    calls: list[ApprovalRequest] = []

    async def no(request: ApprovalRequest) -> ApprovalAnswer:
        calls.append(request)
        return ApprovalAnswer("no", "user")

    if path == "enable":
        save_connectors(
            aldwin_home, {"github": _local("github", approved=False, enabled=False)}
        )
    rig = await make_rig()
    before = (
        connectors_path(aldwin_home).read_bytes()
        if connectors_path(aldwin_home).exists()
        else None
    )
    with pytest.raises(ConnectorNotApproved) as info:
        if path == "add":
            await rig.manager.add(
                _local(approved=False), token=None, dry_run=False, approve=no
            )
        else:
            await rig.manager.enable("github", approve=no)
    title = "Fixture" if path == "add" else "Github"
    assert info.value.message == f"{title} was not approved, so nothing was saved."
    assert len(calls) == 1
    after = (
        connectors_path(aldwin_home).read_bytes()
        if connectors_path(aldwin_home).exists()
        else None
    )
    assert after == before
    assert not stdio._LIVE_PGIDS


# --- import -------------------------------------------------------------------


IMPORTS = Path(__file__).resolve().parents[1] / "fixtures" / "import"


async def test_running_chat_reconciles_imported_entries(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    from aldwin.client import connectors_import_command

    rig = await make_rig()
    result = connectors_import_command(IMPORTS / "cursor.json", home=aldwin_home)
    assert result["imported"] == 2
    rig.model.add_turns(final("ok"))
    await collect(rig.client.turn("hello"))
    listed = {c.name: c for c in (await rig.client.connectors_list()).items}
    assert set(listed) == {"docs", "github"}
    github = listed["github"]
    assert (github.status, github.note, github.transport) == (
        "disabled",
        "review and enable",
        "stdio",
    )
    assert not stdio._LIVE_PGIDS


async def test_enabling_an_import_clears_its_note(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    imported = _local(
        "fixture", approved=False, enabled=False, note="review and enable"
    )
    save_connectors(aldwin_home, {"fixture": imported})
    rig = await make_rig()
    await rig.client.connectors_enable("fixture", on_approval=_answer("yes"))
    assert load_connectors(aldwin_home).entries["fixture"].note is None


async def test_door_gone_during_approval_denies(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    rig = await make_rig()
    asked = asyncio.Event()

    async def wait_forever(_event: ApprovalEvent) -> tuple[Any, str | None]:
        asked.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    add = asyncio.create_task(
        rig.client.connectors_add(
            command=_fixture_command(), name="fixture", on_approval=wait_forever
        )
    )
    try:
        async with asyncio.timeout(10):
            await asked.wait()
            await rig.client.close()
            while not any(
                line["source"] == "no_approver" for line in _log_lines(aldwin_home)
            ):
                await asyncio.sleep(0.05)
    finally:
        add.cancel()
        await asyncio.gather(add, return_exceptions=True)
    line = _log_lines(aldwin_home)[-1]
    assert (line["answer"], line["tool"]) == ("no", "connectors.add")
    assert not connectors_path(aldwin_home).exists()
    assert not stdio._LIVE_PGIDS


async def test_connectors_import_request_finds_then_imports(
    make_rig: RigFactory, aldwin_home: Path
) -> None:
    from aldwin.core.connectors.import_sources import (
        load_import_sources,
        resolve_locations,
    )

    rig = await make_rig()
    desktop = next(s for s in load_import_sources()[0] if s.app == "Claude Desktop")
    target = resolve_locations(
        desktop, user_home=Path.home(), workspace=rig.workspace.root
    )[0][0].path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((IMPORTS / "claude_desktop.json").read_bytes())

    scan = await rig.client.connectors_import(dry_run=True)
    assert [(f.app, f.scope, f.servers) for f in scan.found] == [
        ("Claude Desktop", "user", 1)
    ]
    assert scan.rows == [] and not connectors_path(aldwin_home).exists()

    result = await rig.client.connectors_import([f.path for f in scan.found])
    assert (result.imported, result.values) == (1, 0)
    assert [(r.name, r.status, r.app) for r in result.rows] == [
        ("filesystem", "imported", "Claude Desktop")
    ]
    listed = {c.name: c for c in (await rig.client.connectors_list()).items}
    assert listed["filesystem"].status == "disabled"
    assert listed["filesystem"].note == "review and enable"
    assert not stdio._LIVE_PGIDS
