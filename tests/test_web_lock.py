"""The ``state/web.lock`` file that marks a running ``aldwin web``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aldwin.web.lock import WebProcessLock, running_web


def _dead_pid() -> int:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    return child.pid


def test_lock_path_is_state_web_lock(tmp_path: Path) -> None:
    """TC-15: the lock lives at ``state/web.lock``, apart from turn locks."""
    assert WebProcessLock(tmp_path).path == tmp_path / "state" / "web.lock"


def test_acquire_records_pid_host_port_and_start(tmp_path: Path) -> None:
    """TC-16: the folder is created and the record is readable JSON."""
    lock = WebProcessLock(tmp_path)
    assert lock.try_acquire(host="127.0.0.1", port=7841)
    try:
        record = lock.read()
        assert record is not None
        assert record["pid"] == os.getpid()
        assert record["host"] == "127.0.0.1"
        assert record["port"] == 7841
        assert record["started_at"].endswith("+00:00")
        assert (lock.path.parent.stat().st_mode & 0o777) == 0o700
        assert (lock.path.stat().st_mode & 0o777) == 0o600
    finally:
        lock.release()


def test_second_acquire_fails_while_held(tmp_path: Path) -> None:
    """TC-17: a second holder is refused and the first record is untouched."""
    first = WebProcessLock(tmp_path)
    assert first.try_acquire(host="127.0.0.1", port=7841)
    try:
        second = WebProcessLock(tmp_path)
        assert not second.try_acquire(host="127.0.0.1", port=9999)
        assert not second.held
        record = second.read()
        assert record is not None and record["port"] == 7841
    finally:
        first.release()


def test_lock_left_by_a_dead_process_is_taken_over(tmp_path: Path) -> None:
    """TC-18: a record from a crashed run does not block the next one."""
    path = tmp_path / "state" / "web.lock"
    path.parent.mkdir(parents=True)
    stale = {"pid": _dead_pid(), "host": "127.0.0.1", "port": 7841}
    path.write_text(json.dumps(stale), encoding="utf-8")
    assert running_web(tmp_path) is None

    lock = WebProcessLock(tmp_path)
    assert lock.try_acquire(host="127.0.0.1", port=7842)
    try:
        record = lock.read()
        assert record is not None
        assert record["pid"] == os.getpid() and record["port"] == 7842
    finally:
        lock.release()


def test_release_is_idempotent_and_clears_the_record(tmp_path: Path) -> None:
    """TC-19: release twice is harmless; the next run can take the lock."""
    lock = WebProcessLock(tmp_path)
    assert lock.try_acquire(host="127.0.0.1", port=7841)
    lock.release()
    lock.release()
    assert not lock.held
    assert lock.read() is None
    assert running_web(tmp_path) is None
    assert WebProcessLock(tmp_path).try_acquire(host="127.0.0.1", port=7841)


@pytest.mark.parametrize(
    "content",
    [None, "", "not json", "[]", '{"port": 1}', '{"pid": "1"}', '{"pid": 0}'],
)
def test_read_is_safe_on_missing_or_bad_files(
    tmp_path: Path, content: str | None
) -> None:
    """TC-20: a missing, empty or corrupt file reads as no record."""
    lock = WebProcessLock(tmp_path)
    if content is not None:
        lock.path.parent.mkdir(parents=True)
        lock.path.write_text(content, encoding="utf-8")
    assert lock.read() is None
    assert running_web(tmp_path) is None


def test_running_web_reports_a_live_holder(tmp_path: Path) -> None:
    lock = WebProcessLock(tmp_path)
    assert lock.try_acquire(host="::1", port=8000)
    try:
        record = running_web(tmp_path)
        assert record is not None
        assert record["host"] == "::1" and record["port"] == 8000
    finally:
        lock.release()


_HOLDER = """
import sys
from pathlib import Path
from aldwin.web.lock import WebProcessLock
lock = WebProcessLock(Path(sys.argv[1]))
print("held" if lock.try_acquire(host="127.0.0.1", port=7841) else "busy", flush=True)
sys.stdin.readline()
"""


def test_lock_is_shared_across_processes(tmp_path: Path) -> None:
    """TC-17: a lock held by another process refuses this one."""
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        assert not WebProcessLock(tmp_path).try_acquire(host="127.0.0.1", port=1)
        record = running_web(tmp_path)
        assert record is not None and record["pid"] == holder.pid
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.wait(timeout=30)
    assert WebProcessLock(tmp_path).try_acquire(host="127.0.0.1", port=1)
