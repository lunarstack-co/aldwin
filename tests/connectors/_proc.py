"""Helpers for tests that start real processes."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path

FIXTURE_SERVER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "mcp_stdio_server.py"
)


def fixture_argv(*switches: str) -> list[str]:
    """How to start the fixture stdio server with this interpreter."""
    return [sys.executable, "-I", str(FIXTURE_SERVER), *switches]


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            return stat.read_text().rsplit(")", 1)[1].split()[0] == "Z"
        except (OSError, IndexError):
            return False
    try:
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False
    return state.startswith("Z") or not state


def wait_until_gone(pids: Iterable[int], seconds: float) -> float:
    """Wait for every pid to exit (a zombie counts as gone); return the time taken.

    Raises `AssertionError` naming the survivors when the deadline passes.
    """
    pending = set(pids)
    started = time.monotonic()
    while pending:
        pending = {pid for pid in pending if not _gone(pid)}
        elapsed = time.monotonic() - started
        if pending and elapsed > seconds:
            raise AssertionError(f"still running after {seconds}s: {sorted(pending)}")
        if pending:
            time.sleep(0.05)
    return time.monotonic() - started


async def wait_until_gone_async(pids: Iterable[int], seconds: float) -> float:
    """`wait_until_gone` for cleanup still running on the event loop."""
    import asyncio

    pending = set(pids)
    started = time.monotonic()
    while pending:
        pending = {pid for pid in pending if not _gone(pid)}
        elapsed = time.monotonic() - started
        if pending and elapsed > seconds:
            raise AssertionError(f"still running after {seconds}s: {sorted(pending)}")
        if pending:
            await asyncio.sleep(0.05)
    return time.monotonic() - started
