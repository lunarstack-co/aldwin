"""A local server never sees, logs or leaks a secret it wasn't given."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from canary import CANARY, scan_for_canary
from connectors._proc import fixture_argv
from fakes.model import FakeModel

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.connectors import stdio
from aldwin.core.connectors.config import connectors_path
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.core.workspace import Workspace
from aldwin.host import AldwinHost, InProcessTransport


async def _yes(_event: Any) -> tuple[Any, str | None]:
    return "yes", None


async def test_canary_never_leaves_for_a_local_server(
    fake_model: FakeModel,
    aldwin_home: Path,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CANARY, "1")
    monkeypatch.setenv("ALDWIN_CANARY", CANARY)
    sent: list[Any] = []
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda name, ask: build_agent(
            AldwinConfig(model=name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        ),
        workspace=workspace.root,
    )
    original = transport.send_event

    async def record(event: Any) -> None:
        sent.append(event)
        await original(event)

    transport.send_event = record  # type: ignore[method-assign]
    host._approvals._send_event = record
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    outputs: list[str] = []
    try:
        import shlex

        await client.connectors_add(
            command=shlex.join(fixture_argv()), name="fixture", on_approval=_yes
        )
        mgr = host._connectors
        for tool, args in (("env", {}), ("say_stderr", {"text": CANARY})):
            found = mgr._index.get("fixture", tool)
            assert found is not None
            mgr.load([found])
            wrapped = host._agent_session.tools[f"mcp__fixture__{tool}"]
            outputs.append(await wrapped.inner_tool.forward(**args))  # type: ignore[attr-defined]
    finally:
        await client.close()
        await host.stop_host()
        stdio.kill_live_groups()

    env = json.loads(outputs[0])
    assert CANARY not in env and "ALDWIN_CANARY" not in env
    assert outputs[1] == "ok"
    # The canary was in Aldwin's environment and in one tool argument. The
    # server's own stderr log may echo that argument; nothing else may hold it.
    logs = aldwin_home / "logs"
    hits = scan_for_canary(
        [
            connectors_path(aldwin_home),
            aldwin_home / "sessions",
            logs / "approvals.jsonl",
            logs / "aldwin.log",
        ],
        [outputs[0], *(e.model_dump_json() for e in sent if e.kind != "tool_call")],
    )
    assert hits == []
    assert stat.S_IMODE(os.stat(logs / "mcp-fixture.log").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(logs).st_mode) == 0o700


def test_imported_canary_lives_only_in_the_store(
    aldwin_home: Path, tmp_path: Path
) -> None:
    from aldwin.client import connectors_import_command
    from aldwin.core.connectors.config import credentials_dir

    source = tmp_path / "mcp.json"
    servers = {
        "local": {"command": "srv", "env": {"SRV_TOKEN": CANARY}},
        "remote": {
            "url": "https://r.example/mcp",
            "headers": {"Authorization": f"Bearer {CANARY}"},
        },
    }
    source.write_text(json.dumps({"mcpServers": servers}), "utf-8")
    result = connectors_import_command(source, home=aldwin_home)

    assert result["imported"] == 2
    hits = scan_for_canary(
        [connectors_path(aldwin_home), aldwin_home / "logs", aldwin_home / "sessions"],
        [json.dumps(result)],
    )
    assert hits == []
    folder = credentials_dir(aldwin_home)
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    for name in ("local", "remote"):
        path = folder / f"{name}.json"
        assert CANARY in path.read_text("utf-8")
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
