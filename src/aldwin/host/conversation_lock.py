"""A lock that lets one Aldwin process at a time run a turn in a conversation."""

from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType

from aldwin.core.home import DIR_MODE, FILE_MODE


class ConversationBusyError(Exception):
    """Another process holds the lock for this conversation."""


class ConversationLock:
    """Exclusive ``flock`` on ``state/locks/<session_id>.lock``, held during a turn."""

    def __init__(self, home: Path, session_id: str) -> None:
        if not session_id or os.sep in session_id or session_id.startswith("."):
            raise ValueError(f"Not a session id: {session_id!r}")
        self._path = home / "state" / "locks" / f"{session_id}.lock"
        self._fd: int | None = None

    @property
    def path(self) -> Path:
        """Return the lock file path."""
        return self._path

    @property
    def held(self) -> bool:
        """Return whether this instance holds the lock."""
        return self._fd is not None

    def try_acquire(self) -> bool:
        """Take the lock without waiting; return whether it is now held."""
        import fcntl

        if self._fd is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        """Release the lock; safe to call when it is not held."""
        import fcntl

        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> ConversationLock:
        if not self.try_acquire():
            raise ConversationBusyError(f"{self._path.name} is held elsewhere")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
