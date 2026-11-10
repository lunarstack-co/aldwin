"""Run a real in-process host with the fixture local connector, for signal tests.

Usage: run_host_with_local_connector.py <aldwin home> <workspace> [mode]

Modes:
* (none): wait until the connector is connected, print one JSON line
  `{"host": pid, "server": pid}`, then wait forever.
* `--add-and-wait`: add the fixture as a local command and block inside the
  approval, printing `{"asking": true}` once the question arrives.
* `--atexit`: start a transport and `sys.exit(0)` without leaving it, printing
  `{"server": pid}` first; only the `atexit` backstop can stop it.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Literal

FIXTURE = Path(__file__).with_name("mcp_stdio_server.py")
ARGV = [sys.executable, "-I", str(FIXTURE)]


def _say(payload: dict[str, Any]) -> None:
    print(json.dumps(payload), flush=True)


async def _connected(home: Path, workspace: Path) -> None:
    from aldwin.host.in_process import open_in_process_client

    async with open_in_process_client(home=home, workspace=workspace) as client:
        while True:
            listing = await client.connectors_list()
            if any(i.status == "connected" for i in listing.items):
                break
            await asyncio.sleep(0.05)
        from aldwin.core.connectors import stdio

        [server] = list(stdio._LIVE_PGIDS)
        import os

        _say({"host": os.getpid(), "server": server})
        await asyncio.Event().wait()


async def _add_and_wait(home: Path, workspace: Path) -> None:
    from aldwin.host.in_process import open_in_process_client

    async def handler(_event: Any) -> tuple[Literal["no"], str | None]:
        _say({"asking": True})
        await asyncio.Event().wait()
        return "no", None

    # The tag lets the test find this run's server among parallel workers.
    argv = [*ARGV, f"--tag={home}"]
    async with open_in_process_client(home=home, workspace=workspace) as client:
        await client.connectors_add(
            command=shlex.join(argv), name="fixture", on_approval=handler
        )


async def _atexit(home: Path, workspace: Path) -> None:
    from aldwin.core.connectors.config import ConnectorConfig
    from aldwin.core.connectors.credentials import FileCredentialStore
    from aldwin.core.connectors.local import launch_spec
    from aldwin.core.connectors.stderr_log import ServerLog
    from aldwin.core.connectors.stdio import StdioTransport
    from aldwin.host.in_process import _register_exit_backstop

    _register_exit_backstop()
    config = ConnectorConfig(name="fixture", command=ARGV[0], args=ARGV[1:])
    spec = launch_spec(
        config,
        workspace_root=workspace,
        home=home,
        environ={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        credentials=FileCredentialStore(home),
    )
    transport = StdioTransport(spec, log=ServerLog(home, "fixture"))
    await transport.__aenter__()
    _say({"server": transport.pid})
    sys.exit(0)


def main() -> None:
    home, workspace = Path(sys.argv[1]), Path(sys.argv[2])
    mode = sys.argv[3] if len(sys.argv) > 3 else ""
    run = {"--add-and-wait": _add_and_wait, "--atexit": _atexit}.get(mode, _connected)
    try:
        asyncio.run(run(home, workspace))
    except KeyboardInterrupt:
        # SIGINT: the host's `finally` already stopped the servers.
        sys.exit(130)


if __name__ == "__main__":
    main()
