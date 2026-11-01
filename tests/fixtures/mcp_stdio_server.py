"""A small MCP server over stdio for local-connector tests.

Started as `python -I mcp_stdio_server.py [switches]`. On start it runs a child
`sleep 300` that inherits stdout, so tests can check the whole process group is
cleaned up even when the child holds the pipe.

Switches: `--ignore-sigterm` (ignores SIGTERM and keeps running after stdin
closes, so only SIGKILL ends it), `--silent` (never answers
`initialize`), `--stdout-noise` (one non-JSON line on stdout before serving).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

from mcp.server.mcpserver import MCPServer

FLAGS = set(sys.argv[1:])

if "--ignore-sigterm" in FLAGS:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

_child = subprocess.Popen(["sleep", "300"])

server = MCPServer("fixture")


@server.tool()
def env() -> dict[str, str]:
    """Return this process's environment."""
    return dict(os.environ)


@server.tool()
def cwd() -> str:
    """Return this process's working folder."""
    return os.getcwd()


@server.tool()
def pids() -> dict[str, int]:
    """Return this process's pid and its child's."""
    return {"pid": os.getpid(), "child": _child.pid}


@server.tool()
def say_stderr(text: str) -> str:
    """Write one line to stderr."""
    print(text, file=sys.stderr, flush=True)
    return "ok"


if __name__ == "__main__":
    if "--stdout-noise" in FLAGS:
        print("this is not json", flush=True)
    if "--silent" in FLAGS:
        sys.stdin.read()
    else:
        server.run()
    if "--ignore-sigterm" in FLAGS:
        signal.pause()
