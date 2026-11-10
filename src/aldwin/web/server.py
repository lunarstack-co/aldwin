"""Bind a loopback socket and serve the web app on it with uvicorn."""

from __future__ import annotations

import signal
import socket
from collections.abc import Callable
from types import FrameType
from typing import TYPE_CHECKING

from aldwin.web.auth import bind_address

if TYPE_CHECKING:
    from fastapi import FastAPI

DEFAULT_PORT = 7841


def bind_socket(host: str, port: int | None) -> socket.socket:
    """Bind and listen on ``host``.

    With ``port=None`` this tries ``DEFAULT_PORT`` and falls back to a free
    port the OS picks. An explicit port that is busy raises ``OSError``.
    """
    address = bind_address(host)
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    if port is not None:
        return _listen(family, address, port)
    try:
        return _listen(family, address, DEFAULT_PORT)
    except OSError:
        return _listen(family, address, 0)


def _listen(family: socket.AddressFamily, address: str, port: int) -> socket.socket:
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        # Lets a restart reuse the port while old connections sit in TIME_WAIT.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def serve(app: FastAPI, sock: socket.socket, on_ready: Callable[[], None]) -> None:
    """Serve ``app`` on ``sock`` until Ctrl-C or SIGTERM, then return."""
    import uvicorn

    config = uvicorn.Config(
        app,
        log_level="warning",
        access_log=False,
        server_header=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    # uvicorn re-raises the signal after a clean shutdown; the default SIGTERM
    # action would kill the process before the caller's cleanup runs.
    previous = signal.signal(signal.SIGTERM, _interrupt)
    try:
        on_ready()
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        sock.close()


def _interrupt(_signum: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt
