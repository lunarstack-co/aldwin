"""The turn-scoped conversation lock under ``state/locks/``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from aldwin.host.conversation_lock import ConversationBusyError, ConversationLock

SESSION = "2026-11-10_abcd1234"


def test_lock_path_is_under_state_locks(tmp_path: Path) -> None:
    """TC-07: one lock file per conversation, named after its id."""
    lock = ConversationLock(tmp_path, SESSION)
    assert lock.path == tmp_path / "state" / "locks" / f"{SESSION}.lock"


@pytest.mark.parametrize("bad", ["", "../config", "a/b", ".hidden"])
def test_lock_rejects_ids_that_are_not_file_names(tmp_path: Path, bad: str) -> None:
    """An id that could reach outside the locks folder is refused."""
    with pytest.raises(ValueError):
        ConversationLock(tmp_path, bad)


def test_acquire_creates_private_folder_and_file(tmp_path: Path) -> None:
    """TC-08: the folder is 0700 and the lock file 0600."""
    lock = ConversationLock(tmp_path, SESSION)
    assert lock.try_acquire()
    try:
        assert (lock.path.parent.stat().st_mode & 0o777) == 0o700
        assert (lock.path.stat().st_mode & 0o777) == 0o600
    finally:
        lock.release()


def test_second_instance_cannot_acquire_while_held(tmp_path: Path) -> None:
    """TC-09: a second holder for the same conversation is refused."""
    first = ConversationLock(tmp_path, SESSION)
    second = ConversationLock(tmp_path, SESSION)
    assert first.try_acquire()
    try:
        assert not second.try_acquire()
        assert not second.held
        assert ConversationLock(tmp_path, "2026-11-10_other123").try_acquire()
    finally:
        first.release()


def test_release_is_idempotent_and_lock_can_be_taken_again(tmp_path: Path) -> None:
    """TC-10: releasing twice is harmless; the next holder gets the lock."""
    first = ConversationLock(tmp_path, SESSION)
    assert first.try_acquire()
    assert first.try_acquire()
    first.release()
    first.release()
    assert not first.held

    second = ConversationLock(tmp_path, SESSION)
    assert second.try_acquire()
    second.release()


def test_leftover_lock_file_does_not_block(tmp_path: Path) -> None:
    """TC-11: an empty lock file left by a crashed process is free to take."""
    path = tmp_path / "state" / "locks" / f"{SESSION}.lock"
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")

    lock = ConversationLock(tmp_path, SESSION)
    assert lock.try_acquire()
    lock.release()


def test_context_manager_acquires_and_releases(tmp_path: Path) -> None:
    """TC-12: the with-block holds the lock and frees it on exit."""
    with ConversationLock(tmp_path, SESSION) as lock:
        assert lock.held
        with pytest.raises(ConversationBusyError):
            with ConversationLock(tmp_path, SESSION):
                pass
    assert not lock.held
    assert ConversationLock(tmp_path, SESSION).try_acquire()


_CHILD = """
import sys, time
from pathlib import Path
from aldwin.host.conversation_lock import ConversationLock
lock = ConversationLock(Path(sys.argv[1]), sys.argv[2])
print("held" if lock.try_acquire() else "busy", flush=True)
if lock.held:
    sys.stdin.readline()
"""


def test_lock_is_shared_across_processes(tmp_path: Path) -> None:
    """TC-03: two processes, as the terminal and the browser door are, contend."""
    lock = ConversationLock(tmp_path, SESSION)
    assert lock.try_acquire()
    try:
        busy = subprocess.run(
            [sys.executable, "-c", _CHILD, str(tmp_path), SESSION],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        assert busy.stdout.strip() == "busy"
    finally:
        lock.release()

    holder = subprocess.Popen(
        [sys.executable, "-c", _CHILD, str(tmp_path), SESSION],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        assert not ConversationLock(tmp_path, SESSION).try_acquire()
    finally:
        assert holder.stdin is not None
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.wait(timeout=30)
    assert ConversationLock(tmp_path, SESSION).try_acquire()
