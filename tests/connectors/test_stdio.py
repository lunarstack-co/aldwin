"""`StdioTransport` against the real fixture server: tools, exits, process groups."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from aldwin.core.connectors import stdio
from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.credentials import FileCredentialStore
from aldwin.core.connectors.errors import (
    ConnectorCommandNotFound,
    ConnectorProcessExited,
)
from aldwin.core.connectors.local import LaunchSpec, launch_spec
from aldwin.core.connectors.stderr_log import LINE_CUT, MAX_LINE_CHARS, ServerLog
from aldwin.core.connectors.stdio import StdioTransport
from aldwin.core.workspace import Workspace

from ._proc import fixture_argv, wait_until_gone


def _spec(
    workspace: Workspace, home: Path, *switches: str, command: str | None = None
) -> LaunchSpec:
    argv = fixture_argv(*switches)
    config = ConnectorConfig(name="fixture", command=command or argv[0], args=argv[1:])
    return launch_spec(
        config,
        workspace_root=workspace.root,
        home=home,
        environ=os.environ,
        credentials=FileCredentialStore(home),
    )


def _transport(
    workspace: Workspace, home: Path, *switches: str, term_grace: float = 1.0
) -> StdioTransport:
    return StdioTransport(
        _spec(workspace, home, *switches),
        log=ServerLog(home, "fixture"),
        term_grace=term_grace,
    )


async def _call(client: Any, tool: str, **args: Any) -> Any:
    result = await client.call_tool(tool, args)
    if result.structured_content is not None:
        content = result.structured_content
        return content.get("result", content) if isinstance(content, dict) else content
    text = result.content[0].text
    try:
        return json.loads(text)
    except ValueError:
        return text


async def test_cwd_tool_returns_spec_cwd(
    workspace: Workspace, aldwin_home: Path
) -> None:
    async with asyncio.timeout(30):
        async with Client(_transport(workspace, aldwin_home), mode="legacy") as client:
            cwd = await _call(client, "cwd")
    assert Path(cwd).resolve() == workspace.root.resolve()


async def test_exit_kills_leader_and_child_within_5s(
    workspace: Workspace, aldwin_home: Path
) -> None:
    transport = _transport(workspace, aldwin_home, term_grace=3.0)
    async with asyncio.timeout(30):
        async with Client(transport, mode="legacy") as client:
            pids = await _call(client, "pids")
        assert wait_until_gone([pids["pid"], pids["child"]], 5.0) < 5.0
    assert not stdio._LIVE_PGIDS


async def test_clean_leader_exit_still_kills_group(
    workspace: Workspace, aldwin_home: Path
) -> None:
    """The leader exits 0 on stdin EOF; its `sleep` child must go too."""
    transport = _transport(workspace, aldwin_home, term_grace=3.0)
    async with asyncio.timeout(30):
        async with Client(transport, mode="legacy") as client:
            pids = await _call(client, "pids")
    assert transport._process is not None and transport._process.returncode == 0
    wait_until_gone([pids["pid"], pids["child"]], 5.0)


async def test_sigterm_ignored_escalates_to_sigkill(
    workspace: Workspace, aldwin_home: Path
) -> None:
    transport = StdioTransport(
        _spec(workspace, aldwin_home, "--ignore-sigterm"),
        log=ServerLog(aldwin_home, "fixture"),
        term_grace=1.0,
        stdin_grace=0.1,
    )
    async with asyncio.timeout(30):
        async with Client(transport, mode="legacy") as client:
            pids = await _call(client, "pids")
            started = time.monotonic()
    wait_until_gone([pids["pid"], pids["child"]], 2.5)
    assert time.monotonic() - started < 3.0
    assert transport._process is not None
    assert transport._process.returncode == -signal.SIGKILL


async def test_leader_kill9_raises_process_exited_within_5s(
    workspace: Workspace, aldwin_home: Path
) -> None:
    transport = _transport(workspace, aldwin_home)
    started = 0.0
    with pytest.raises(BaseException) as info:
        async with asyncio.timeout(30):
            async with Client(transport, mode="legacy") as client:
                pids = await _call(client, "pids")
                os.kill(pids["pid"], signal.SIGKILL)
                started = time.monotonic()
                await asyncio.sleep(20)
    assert time.monotonic() - started < 5.0
    leaves = _leaves(info.value)
    exited = [e for e in leaves if isinstance(e, ConnectorProcessExited)]
    assert exited and exited[0].message == "server exited (code -9)"
    wait_until_gone([pids["child"]], 5.0)


async def test_unknown_command_raises_command_not_found(
    workspace: Workspace, aldwin_home: Path
) -> None:
    spec = _spec(workspace, aldwin_home, command="does-not-exist")
    transport = StdioTransport(spec, log=ServerLog(aldwin_home, "fixture"))
    with pytest.raises(ConnectorCommandNotFound) as info:
        await transport.__aenter__()
    assert info.value.message == "command not found: does-not-exist"
    assert transport.pid is None and not stdio._LIVE_PGIDS


async def test_relative_command_path_is_checked(
    workspace: Workspace, aldwin_home: Path
) -> None:
    spec = _spec(workspace, aldwin_home, command="./missing-tool")
    with pytest.raises(ConnectorCommandNotFound):
        await StdioTransport(spec, log=ServerLog(aldwin_home, "x")).__aenter__()


async def test_stdout_noise_is_ignored(workspace: Workspace, aldwin_home: Path) -> None:
    async with asyncio.timeout(30):
        async with Client(
            _transport(workspace, aldwin_home, "--stdout-noise"), mode="legacy"
        ) as client:
            cwd = await _call(client, "cwd")
    assert Path(cwd).resolve() == workspace.root.resolve()


async def test_stderr_goes_to_the_server_log(
    workspace: Workspace, aldwin_home: Path
) -> None:
    async with asyncio.timeout(30):
        async with Client(_transport(workspace, aldwin_home), mode="legacy") as client:
            assert await _call(client, "say_stderr", text="hello-log-31") == "ok"
    log = aldwin_home / "logs" / "mcp-fixture.log"
    assert log.read_text("utf-8").splitlines()[-1] == "hello-log-31"


async def test_large_stderr_does_not_block(
    workspace: Workspace, aldwin_home: Path
) -> None:
    async with asyncio.timeout(60):
        async with Client(_transport(workspace, aldwin_home), mode="legacy") as client:
            await _call(client, "say_stderr", text="z" * 2_000_000)
            for _ in range(3):
                await _call(client, "say_stderr", text="\n".join(["w" * 99] * 10_000))
            started = time.monotonic()
            await _call(client, "cwd")
            assert time.monotonic() - started < 5.0
    lines = (aldwin_home / "logs").glob("mcp-fixture.log*")
    longest = max(
        len(line) for path in lines for line in path.read_text("utf-8").splitlines()
    )
    assert longest <= MAX_LINE_CHARS + len(LINE_CUT)


def test_kill_live_groups_signals_and_forgets(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(stdio.os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    stdio._LIVE_PGIDS.update({111, 222})
    stdio.kill_live_groups()
    assert sorted(sent) == [(111, signal.SIGKILL), (222, signal.SIGKILL)]
    assert not stdio._LIVE_PGIDS


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _leaves(sub)]
    return [exc, *([exc.__cause__] if exc.__cause__ else [])]


class _BrokenLog(ServerLog):
    def write_line(self, raw: bytes) -> None:
        raise OSError("disk full")


async def test_unwritable_log_keeps_draining_stderr(
    workspace: Workspace, aldwin_home: Path
) -> None:
    transport = StdioTransport(
        _spec(workspace, aldwin_home), log=_BrokenLog(aldwin_home, "fixture")
    )
    async with asyncio.timeout(30):
        async with Client(transport, mode="legacy") as client:
            for _ in range(3):
                await _call(client, "say_stderr", text="\n".join(["w" * 99] * 2_000))
            cwd = await _call(client, "cwd")
    assert Path(cwd).resolve() == workspace.root.resolve()
