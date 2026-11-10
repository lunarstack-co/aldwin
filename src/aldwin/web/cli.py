"""The ``aldwin web`` command: start the loopback server and open the browser."""

from __future__ import annotations

import webbrowser

import typer

from aldwin import __version__
from aldwin.web.app import StaticFilesMissingError, create_app
from aldwin.web.auth import Authenticator, is_loopback_host, origin_for
from aldwin.web.lock import WebProcessLock
from aldwin.web.server import bind_socket, serve

NOT_LOOPBACK_MESSAGE = (
    "aldwin web only listens on this computer. Use --host 127.0.0.1, localhost or ::1."
)


def run_web(
    *,
    host: str,
    port: int | None,
    open_browser: bool,
    workspace: str | None,
) -> int:
    """Run the web door until it is stopped; return the exit code."""
    if not is_loopback_host(host):
        typer.echo(NOT_LOOPBACK_MESSAGE, err=True)
        return 2

    import aldwin.client as client

    try:
        result = client.bootstrap(plain=True, workspace=workspace)
    except client.WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        return 2
    except (client.PlatformNotSupportedError, client.ClientBootstrapError) as exc:
        typer.echo(str(exc), err=True)
        return 1

    try:
        sock = bind_socket(host, port)
    except OSError as exc:
        target = host if port is None else f"{host} port {port}"
        typer.echo(f"Could not listen on {target}: {exc.strerror}.", err=True)
        return 1
    bound_port: int = sock.getsockname()[1]

    lock = WebProcessLock(result.home)
    if not lock.try_acquire(host=host, port=bound_port):
        sock.close()
        typer.echo(_already_running_message(lock), err=True)
        return 1

    try:
        authenticator = Authenticator.mint(host, bound_port)
        try:
            app = create_app(
                authenticator=authenticator,
                home=result.home,
                home_display=result.display_home,
                version=__version__,
            )
        except StaticFilesMissingError as exc:
            sock.close()
            typer.echo(str(exc), err=True)
            return 1

        def announce() -> None:
            typer.echo(f"Aldwin web is running at {authenticator.origin}")
            typer.echo(f"Open this link to start: {authenticator.launch_url}")
            typer.echo("Press Ctrl-C to stop.")
            if open_browser:
                webbrowser.open(authenticator.launch_url)

        serve(app, sock, announce)
    finally:
        lock.release()
    return 0


def _already_running_message(lock: WebProcessLock) -> str:
    record = lock.read()
    if record is None:
        return "aldwin web is already running for this Aldwin home."
    where = origin_for(str(record.get("host", "127.0.0.1")), record.get("port", 0))
    return (
        f"aldwin web is already running at {where} (pid {record['pid']}). "
        "Stop it with Ctrl-C in its terminal first."
    )
