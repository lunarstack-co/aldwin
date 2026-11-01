"""A local server's stderr, kept in `~/.aldwin/logs/mcp-<name>.log`.

Every file is 0600, secrets are redacted line by line, long lines are cut, and
the log rotates at 1 MB keeping 3 backups, like `aldwin.log`. Nothing written
here ever reaches the conversation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from aldwin.core.approval_log import redact_secrets
from aldwin.core.home import DIR_MODE, FILE_MODE

MAX_LOG_BYTES: Final[int] = 1_000_000
LOG_BACKUPS: Final[int] = 3
MAX_LINE_CHARS: Final[int] = 8192
LINE_CUT: Final[str] = "… [line cut]"


def server_log_path(home: Path, name: str) -> Path:
    return home / "logs" / f"mcp-{name}.log"


class ServerLog:
    """An append-only, rotating, private log for one local server."""

    def __init__(
        self,
        home: Path,
        name: str,
        *,
        max_bytes: int = MAX_LOG_BYTES,
        backups: int = LOG_BACKUPS,
    ) -> None:
        self.path = server_log_path(home, name)
        self._max_bytes = max_bytes
        self._backups = backups
        self._fd: int | None = None
        self._size = 0

    def write_line(self, raw: bytes) -> None:
        """Append one line of the server's stderr."""
        text = redact_secrets(raw.decode("utf-8", "replace").rstrip("\r\n"))
        if len(text) > MAX_LINE_CHARS:
            text = text[:MAX_LINE_CHARS] + LINE_CUT
        data = f"{text}\n".encode()
        fd = self._open()
        if self._size and self._size + len(data) > self._max_bytes:
            self._rotate()
            fd = self._open()
        os.write(fd, data)
        self._size += len(data)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _open(self) -> int:
        if self._fd is None:
            folder = self.path.parent
            folder.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
            os.chmod(folder, DIR_MODE)
            self._fd = os.open(
                self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE
            )
            # A file left by an older run may have looser permissions.
            os.fchmod(self._fd, FILE_MODE)
            self._size = os.fstat(self._fd).st_size
        return self._fd

    def _rotate(self) -> None:
        self.close()
        oldest = self.path.with_name(f"{self.path.name}.{self._backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self._backups - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                source.rename(self.path.with_name(f"{self.path.name}.{index + 1}"))
        self.path.rename(self.path.with_name(f"{self.path.name}.1"))
        self._size = 0
