"""The ``state/web.lock`` file that marks a running ``aldwin web``."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DIR_MODE = 0o700
_FILE_MODE = 0o600


class WebProcessLock:
    """Exclusive ``flock`` on ``state/web.lock``, holding the server's pid and URL.

    The kernel drops the lock when the process dies, so a lock left by a crash
    is taken over by the next ``aldwin web``.
    """

    def __init__(self, home: Path) -> None:
        self._path = home / "state" / "web.lock"
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def held(self) -> bool:
        return self._fd is not None

    def read(self) -> dict[str, Any] | None:
        """Return the recorded ``pid``, ``host``, ``port`` and ``started_at``.

        Returns ``None`` when the file is missing, empty or unreadable.
        """
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        pid = data.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return None
        return data

    def try_acquire(self, *, host: str, port: int) -> bool:
        """Take the lock without waiting and record this process in it."""
        import fcntl

        if self._fd is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, _FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        record = {
            "pid": os.getpid(),
            "host": host,
            "port": port,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(record).encode("utf-8"))
        self._fd = fd
        return True

    def release(self) -> None:
        """Clear the record and release the lock; safe to call when not held."""
        import fcntl

        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            os.ftruncate(fd, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def running_web(home: Path) -> dict[str, Any] | None:
    """Return the lock record of a live ``aldwin web``, or ``None``."""
    record = WebProcessLock(home).read()
    if record is None or not _pid_alive(record["pid"]):
        return None
    return record


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
