"""A local server's private stderr log: modes, rotation, redaction, line cap."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from aldwin.core.connectors.stderr_log import LINE_CUT, MAX_LINE_CHARS, ServerLog


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _write_bytes(log: ServerLog, total: int) -> None:
    line = b"x" * 99
    for _ in range(total // 100):
        log.write_line(line)


def test_rotation_keeps_three_backups_0600(aldwin_home: Path) -> None:
    log = ServerLog(aldwin_home, "x")
    _write_bytes(log, 1_200_000)
    first = sorted(p.name for p in log.path.parent.glob("mcp-x.log*"))
    assert first == ["mcp-x.log", "mcp-x.log.1"]
    assert all(_mode(log.path.parent / n) == 0o600 for n in first)

    _write_bytes(log, 6_000_000)
    log.close()
    files = sorted(p.name for p in log.path.parent.glob("mcp-x.log*"))
    assert files == ["mcp-x.log", "mcp-x.log.1", "mcp-x.log.2", "mcp-x.log.3"]
    assert all(_mode(log.path.parent / n) == 0o600 for n in files)
    assert _mode(log.path.parent) == 0o700


def test_redacts_and_caps_lines(aldwin_home: Path) -> None:
    log = ServerLog(aldwin_home, "x")
    log.write_line(b"token ghp_fake0000000000000000000000000000000000")
    log.write_line(b"y" * 20_000 + b"\n")
    log.write_line(b"\xff\xfe ok")
    log.close()

    first, long_line, bad = log.path.read_text("utf-8").splitlines()
    assert first == "token ‹redacted›"
    assert long_line == "y" * MAX_LINE_CHARS + LINE_CUT
    assert bad.endswith(" ok") and "�" in bad


def test_existing_file_is_tightened_to_0600(aldwin_home: Path) -> None:
    log = ServerLog(aldwin_home, "x")
    log.path.parent.mkdir(parents=True, exist_ok=True)
    log.path.write_text("old\n")
    os.chmod(log.path, 0o644)
    log.write_line(b"new")
    log.close()
    assert _mode(log.path) == 0o600
    assert log.path.read_text().splitlines() == ["old", "new"]
