"""Talk MCP to a local server over its stdin and stdout.

Aldwin starts the program itself instead of using the SDK's `stdio_client`, which
hides the pid, adds parent environment variables, hands the child a raw stderr
fd, and leaves the server's children running when the server exits on its own.
Here the child gets exactly `LaunchSpec.env`, stderr goes through `ServerLog`,
and stopping always signals the whole process group.

The shutdown follows `mcp/client/stdio.py` (poll `returncode`, drain stdout,
shield the stop) and `mcp/os/posix/utilities.py` (EPERM may still be alive).
"""

from __future__ import annotations

import contextlib
import os
import signal
from typing import Any, Final

import anyio
import anyio.lowlevel
import mcp_types
from anyio.abc import Process, TaskGroup
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from anyio.streams.text import TextReceiveStream
from mcp.shared.message import SessionMessage

from aldwin.core.connectors.errors import (
    ConnectorCommandNotFound,
    ConnectorProcessExited,
)
from aldwin.core.connectors.local import LaunchSpec, require_command
from aldwin.core.connectors.stderr_log import MAX_LINE_CHARS, ServerLog

_POLL_S: Final[float] = 0.05
_EXIT_WATCH_S: Final[float] = 0.2
_WRITER_FLUSH_S: Final[float] = 0.5
_KILL_REAP_S: Final[float] = 1.0

# Process groups started here and not yet confirmed gone. Read by
# `kill_live_groups`, the backstop for an abandoned close or interpreter exit.
_LIVE_PGIDS: set[int] = set()


def kill_live_groups(sig: int | None = None) -> None:
    """Signal every local server group still running (SIGKILL by default)."""
    chosen = signal.SIGKILL if sig is None else sig
    for pgid in list(_LIVE_PGIDS):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, chosen)
        _LIVE_PGIDS.discard(pgid)


class StdioTransport:
    """An MCP client transport for one local server: entering it starts the
    program, leaving it stops the program and everything it started.
    """

    def __init__(
        self,
        spec: LaunchSpec,
        *,
        log: ServerLog,
        term_grace: float = 3.0,
        stdin_grace: float = 0.5,
    ) -> None:
        self.spec = spec
        self.pid: int | None = None
        self._log = log
        self._term_grace = term_grace
        self._stdin_grace = stdin_grace
        self._process: Process | None = None
        self._group: TaskGroup | None = None
        self._stopping = False
        self._writer_done = anyio.Event()
        self._read_send, self._read_recv = anyio.create_memory_object_stream[
            SessionMessage | Exception
        ](0)
        self._write_send, self._write_recv = anyio.create_memory_object_stream[
            SessionMessage
        ](0)

    async def __aenter__(
        self,
    ) -> tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
    ]:
        require_command(self.spec)
        try:
            process = await anyio.open_process(
                self.spec.argv,
                cwd=self.spec.cwd,
                env=self.spec.env,
                start_new_session=True,
            )
        except (FileNotFoundError, PermissionError):
            raise ConnectorCommandNotFound(self.spec.command) from None
        # No awaits before the task group starts, so a cancellation cannot leak it.
        self._process = process
        self.pid = process.pid
        _LIVE_PGIDS.add(process.pid)
        self._group = anyio.create_task_group()
        await self._group.__aenter__()
        self._group.start_soon(self._read_stdout, process)
        self._group.start_soon(self._write_stdin, process)
        self._group.start_soon(self._pump_stderr, process)
        self._group.start_soon(self._watch_exit, process)
        return self._read_recv, self._write_send

    async def __aexit__(self, *exc: Any) -> bool | None:
        self._stopping = True
        # The stop must finish even when the caller is being cancelled, or the
        # server would outlive Aldwin; every wait inside is bounded.
        with anyio.CancelScope(shield=True):
            await self._shutdown()
        assert self._group is not None
        self._group.cancel_scope.cancel()
        return await self._group.__aexit__(*exc)

    async def _read_stdout(self, process: Process) -> None:
        assert process.stdout is not None
        stdout = TextReceiveStream(process.stdout, encoding="utf-8", errors="replace")
        try:
            async with self._read_send:
                buffer = ""
                async for chunk in stdout:
                    lines = (buffer + chunk).split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            await self._read_send.send(_parse_line(line))
                        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                            return
        except (
            anyio.ClosedResourceError,
            anyio.BrokenResourceError,
            ConnectionError,
            OSError,
        ):
            pass  # the pipe closed mid-read: Aldwin stopped it, or the server died

    async def _write_stdin(self, process: Process) -> None:
        assert process.stdin is not None
        try:
            async with self._write_recv:
                async for message in self._write_recv:
                    data = message.message.model_dump_json(
                        by_alias=True, exclude_unset=True
                    )
                    await process.stdin.send(f"{data}\n".encode())
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, OSError):
            # The server stopped reading: end the session instead of hanging.
            await self._read_send.aclose()
        finally:
            self._writer_done.set()

    async def _pump_stderr(self, process: Process) -> None:
        """Copy stderr to the log line by line, never letting the child block."""
        assert process.stderr is not None
        buffer = b""
        cutting = False  # inside a line already written out as cut
        with contextlib.suppress(
            anyio.EndOfStream,
            anyio.ClosedResourceError,
            anyio.BrokenResourceError,
            OSError,
        ):
            while True:
                buffer += await process.stderr.receive()
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    if not cutting:
                        self._log_line(line)
                    cutting = False
                if len(buffer) > MAX_LINE_CHARS * 4:
                    if not cutting:
                        self._log_line(buffer)
                    cutting = True
                    buffer = b""
        if buffer and not cutting:
            self._log_line(buffer)

    def _log_line(self, line: bytes) -> None:
        # A log that can't be written must not stop the drain, or the server
        # blocks once the stderr pipe fills.
        with contextlib.suppress(OSError):
            self._log.write_line(line)

    async def _watch_exit(self, process: Process) -> None:
        """Turn an exit nobody asked for into an error the connection reports."""
        while process.returncode is None:
            await anyio.sleep(_EXIT_WATCH_S)
        if not self._stopping:
            raise ConnectorProcessExited(process.returncode)

    async def _shutdown(self) -> None:
        process = self._process
        assert process is not None
        self._read_recv.close()
        self._write_send.close()
        with anyio.move_on_after(_WRITER_FLUSH_S):
            await self._writer_done.wait()
        await _close(process.stdin)
        await _wait_exit(process, self._stdin_grace)
        await self._stop_group(process)
        await _close(process.stdout)
        await _close(process.stderr)
        _close_subprocess_transport(process)
        self._log.close()

    async def _stop_group(self, process: Process) -> None:
        """SIGTERM the whole group, even when the leader already exited, because
        its children can still be running; SIGKILL whatever is left.
        """
        pgid = process.pid
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            _LIVE_PGIDS.discard(pgid)
            return
        except PermissionError:
            pass  # zombie or foreign members on macOS; keep waiting and escalate
        if await _wait_group_gone(process, self._term_grace):
            _LIVE_PGIDS.discard(pgid)
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)
        await _wait_group_gone(process, _KILL_REAP_S)
        # After SIGKILL only unreaped zombies can remain; never signal it again.
        _LIVE_PGIDS.discard(pgid)


def _parse_line(line: str) -> SessionMessage | Exception:
    """One stdout line; a line that isn't JSON-RPC is passed on as an error value."""
    try:
        message = mcp_types.jsonrpc_message_adapter.validate_json(line, by_name=False)
    except ValueError as exc:
        return exc
    return SessionMessage(message)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # EPERM never proves the group is gone
    return True


async def _wait_group_gone(process: Process, timeout: float) -> bool:
    with anyio.move_on_after(timeout):
        while _group_alive(process.pid):
            # Reading returncode lets the loop reap a zombie leader.
            _ = process.returncode
            await anyio.sleep(_POLL_S)
        return True
    return False


async def _wait_exit(process: Process, timeout: float) -> bool:
    """Poll `returncode`: `wait()` also waits for pipe EOF, which a child that
    inherited stdout can hold open forever.
    """
    with anyio.move_on_after(timeout):
        while process.returncode is None:
            await anyio.sleep(_POLL_S)
        return True
    return False


async def _close(stream: Any) -> None:
    if stream is None:
        return
    with contextlib.suppress(
        OSError, anyio.BrokenResourceError, anyio.ClosedResourceError
    ):
        await stream.aclose()


def _close_subprocess_transport(process: Process) -> None:
    """Close the asyncio subprocess transport, which otherwise stays open (and
    warns at exit) while a surviving descendant holds a pipe end.
    """
    transport = getattr(getattr(process, "_process", None), "_transport", None)
    close = getattr(transport, "close", None)
    if callable(close):
        with contextlib.suppress(PermissionError):
            close()
