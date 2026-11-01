"""A real host in a subprocess: SIGTERM, SIGINT and exit stop local servers."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from connectors._proc import fixture_argv, wait_until_gone

from aldwin.core.connectors.config import (
    ConnectorConfig,
    connectors_path,
    save_connectors,
)
from aldwin.core.connectors.local import launch_digest
from aldwin.core.workspace import Workspace

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "run_host_with_local_connector.py"
)


def _children(pid: int) -> list[int]:
    out = subprocess.run(
        ["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=30
    ).stdout
    return [int(line) for line in out.split()]


def _start(home: Path, workspace: Workspace, *mode: str) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "ALDWIN_HOME": str(home),
        # The host builds an agent at start; no request is ever sent.
        "OPENAI_API_KEY": "sk-test-not-used",
    }
    return subprocess.Popen(
        [sys.executable, str(SCRIPT), str(home), str(workspace.root), *mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _read_json(proc: subprocess.Popen[str], seconds: float = 30) -> dict[str, Any]:
    assert proc.stdout is not None
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.startswith("{"):
            return dict(json.loads(line))
        if not line and proc.poll() is not None:
            break
    proc.kill()
    stderr = proc.stderr.read() if proc.stderr else ""
    raise AssertionError(f"no JSON line from the host; stderr: {stderr}")


def _approved_fixture(home: Path) -> None:
    argv = fixture_argv()
    config = ConnectorConfig(name="fixture", command=argv[0], args=argv[1:])
    config = config.model_copy(update={"approved_digest": launch_digest(config)})
    save_connectors(home, {"fixture": config})


@pytest.mark.parametrize(
    "sig", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"]
)
def test_host_signal_cleans_up(
    aldwin_home: Path, workspace: Workspace, sig: signal.Signals
) -> None:
    _approved_fixture(aldwin_home)
    proc = _start(aldwin_home, workspace)
    try:
        pids = _read_json(proc)
        server = pids["server"]
        children = _children(server)
        assert children, "the fixture's sleep child should be running"
        os.kill(pids["host"], sig)
        proc.wait(timeout=6)
        wait_until_gone([server, *children], 5.0)
        assert proc.stderr is not None
        assert "Traceback" not in proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)


def test_atexit_kills_live_groups(aldwin_home: Path, workspace: Workspace) -> None:
    proc = _start(aldwin_home, workspace, "--atexit")
    try:
        server = _read_json(proc)["server"]
        children = _children(server)
        proc.wait(timeout=30)
        assert proc.returncode == 0
        wait_until_gone([server, *children], 2.0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)


def test_sigterm_during_approval_starts_nothing(
    aldwin_home: Path, workspace: Workspace
) -> None:
    proc = _start(aldwin_home, workspace, "--add-and-wait")
    try:
        assert _read_json(proc) == {"asking": True}
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=6)
        assert not connectors_path(aldwin_home).exists()
        running = subprocess.run(
            ["pgrep", "-f", "mcp_stdio_server.py"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        assert running.strip() == ""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
