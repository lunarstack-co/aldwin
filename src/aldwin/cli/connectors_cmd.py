"""`aldwin connectors …` shell commands.

Exit codes: 0 on success, 1 on a user error (the message is printed), 2 on
anything unexpected.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import webbrowser
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

import typer

connectors_app = typer.Typer(
    name="connectors",
    help="Add and manage connected apps (MCP servers).",
    no_args_is_help=True,
    add_completion=False,
)

_T = TypeVar("_T")

ADD_NEEDS_YES = "Adding a local command needs approval; rerun with --yes."
ENABLE_NEEDS_YES = "Enabling a local command needs approval; rerun with --yes."
YES_REASON = "approved with --yes"


def _approval_handler(yes: bool) -> Any:
    """Answer the host's question: `--yes` says yes, otherwise ask, No by default."""

    async def handler(event: Any) -> tuple[str, str | None]:
        if yes:
            return "yes", YES_REASON
        from aldwin.client import sanitize_display_text

        typer.echo(sanitize_display_text(event.summary))
        for label in event.risky:
            typer.echo(f"  ! {sanitize_display_text(label)}")
        for line in event.detail.splitlines():
            typer.echo(f"  {line}")
        reply = typer.prompt("Run this program? [y/N]", default="", show_default=False)
        return ("yes", None) if reply.strip().lower() in ("y", "yes") else ("no", None)

    return handler


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def _can_ask(yes: bool) -> bool:
    return yes or _stdin_is_tty()


def _run(action: Callable[[], _T]) -> _T:
    """Run `action`, turning failures into the documented exit codes."""
    from aldwin.client import ConnectorCommandError

    try:
        return action()
    except ConnectorCommandError as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(code=1) from None
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Something went wrong: {exc}", err=True)
        raise typer.Exit(code=2) from None


@connectors_app.command("list")
def list_cmd(
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
) -> None:
    """List connectors."""
    from aldwin.client import connectors_summary

    rows: list[dict[str, Any]] = _run(connectors_summary)
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo(
            "No connectors yet. Add one with: aldwin connectors add <name> --url <url>"
        )
        return
    for row in rows:
        state = "on" if row["enabled"] else "off"
        where = row["url"] or row.get("command") or ""
        typer.echo(f"{row['name']}  {where}  ({state})")


@connectors_app.command("add")
def add_cmd(
    name: str = typer.Argument(..., help="Short name, such as github."),
    url: str | None = typer.Option(None, "--url", help="The server's MCP URL."),
    command: str | None = typer.Option(
        None, "--command", help="A local server's command line (no shell)."
    ),
    env_allow: list[str] = typer.Option(
        [],
        "--env-allow",
        help="An environment variable a local server may see (repeatable).",
    ),
    cwd: str | None = typer.Option(
        None, "--cwd", help="Folder a local server runs in (default: the workspace)."
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Approve running a local command without asking."
    ),
    token_env: str | None = typer.Option(
        None, "--token-env", help="Environment variable that holds the token."
    ),
    client_id: str | None = typer.Option(
        None,
        "--client-id",
        help="An OAuth client ID registered for Aldwin; sign in with `login`.",
    ),
) -> None:
    """Check a server and save it as a connector."""
    if (url is None) == (command is None):
        typer.echo("Give either --url or --command.", err=True)
        raise typer.Exit(code=1)
    if command is not None:
        _add_local(name, command, env_allow=env_allow, cwd=cwd, yes=yes)
        return
    assert url is not None
    from aldwin.client import connectors_add_command

    row = _run(
        lambda: asyncio.run(
            connectors_add_command(name, url, token_env=token_env, client_id=client_id)
        )
    )
    if client_id:
        typer.echo(
            f"{row['title']} added. Sign in with: aldwin connectors login {name}"
        )
        return
    _echo_added(row)


def _add_local(
    name: str, command: str, *, env_allow: list[str], cwd: str | None, yes: bool
) -> None:
    if not _can_ask(yes):
        typer.echo(ADD_NEEDS_YES, err=True)
        raise typer.Exit(code=1)
    from aldwin.client import connectors_add_local_command

    row = _run(
        lambda: asyncio.run(
            connectors_add_local_command(
                name,
                command,
                env_allow=env_allow,
                cwd=cwd,
                on_approval=_approval_handler(yes),
            )
        )
    )
    _echo_added(row)


def _echo_added(row: dict[str, Any]) -> None:
    total = row["read"] + row["write"]
    typer.echo(
        f"{row['title']} added: {total} tools, {row['read']} read (run automatically), "
        f"{row['write']} write (ask first)."
    )


@connectors_app.command("remove")
def remove_cmd(name: str = typer.Argument(...)) -> None:
    """Delete a connector and its saved token."""
    from aldwin.client import connectors_remove_command

    _run(lambda: connectors_remove_command(name))
    typer.echo(f"{name} removed.")


@connectors_app.command("enable")
def enable_cmd(
    name: str = typer.Argument(...),
    yes: bool = typer.Option(
        False, "--yes", help="Approve running a local command without asking."
    ),
) -> None:
    """Turn a connector on. A local command not yet approved asks first."""
    from aldwin.client import connector_needs_approval, connectors_enable_command

    if not _can_ask(yes) and _run(lambda: connector_needs_approval(name)):
        typer.echo(ENABLE_NEEDS_YES, err=True)
        raise typer.Exit(code=1)
    _run(
        lambda: asyncio.run(
            connectors_enable_command(name, on_approval=_approval_handler(yes))
        )
    )
    typer.echo(f"{name} enabled.")


IMPORT_NONE_FOUND = (
    "No MCP config files found in the usual places. "
    "Give a path: aldwin connectors import <path>"
)
IMPORT_NEEDS_YES = "Importing needs confirmation; rerun with --yes."


@connectors_app.command("import")
def import_cmd(
    path: str | None = typer.Argument(
        None,
        help="Another agent app's MCP config file. "
        "Leave it out to look in the usual places.",
    ),
    rename_conflicts: bool = typer.Option(
        False, "--rename-conflicts", help="Save a taken name as name-2 instead."
    ),
    yes: bool = typer.Option(
        False, "--yes", help="Import what was found without asking."
    ),
) -> None:
    """Import MCP servers from another app. Local commands are saved disabled."""
    from pathlib import Path

    # Imported here, not at the top: the view pulls in the client and the
    # host, which `aldwin --version` must not pay for.
    from aldwin.cli.connectors_view import import_summary
    from aldwin.client import ConnectorCommandError, connectors_import_command

    def import_one(source: Path) -> dict[str, Any]:
        return connectors_import_command(source, rename_conflicts=rename_conflicts)

    if path is not None:
        results = [_run(partial(import_one, Path(path).expanduser()))]
    else:
        results = []
        for source in _found_sources(yes):
            # One bad file must not stop the others from importing.
            try:
                results.append(import_one(source))
            except ConnectorCommandError as exc:
                _warn(exc.message)
            except Exception as exc:  # noqa: BLE001
                _warn(f"couldn't import {source} ({type(exc).__name__})")
    for result in results:
        for warning in result["warnings"]:
            _warn(warning)
    _print_import_table(results)
    imported = sum(r["imported"] for r in results)
    typer.echo(import_summary(imported, sum(r["values"] for r in results)))
    if imported == 0:
        raise typer.Exit(code=1)


def _warn(message: str) -> None:
    from aldwin.client import sanitize_display_text

    typer.echo(f"warning: {sanitize_display_text(message)}", err=True)


def _found_sources(yes: bool) -> list[Any]:
    """List the config files found, confirm, and return the readable ones."""
    from pathlib import Path

    from aldwin.client import connectors_find_configs, sanitize_display_text

    report = _run(connectors_find_configs)
    for warning in report["warnings"]:
        _warn(warning)
    found = report["found"]
    if not found:
        typer.echo(IMPORT_NONE_FOUND, err=True)
        raise typer.Exit(code=1)
    typer.echo("Found:")
    for item in found:
        count = item["servers"]
        state = (
            f"can't read: {item['error']}"
            if item["error"]
            else f"{count} server{'' if count == 1 else 's'}"
        )
        typer.echo(
            f"  {item['app']} ({item['scope']})  {state}  "
            f"{sanitize_display_text(item['path'])}"
        )
    usable = [Path(i["path"]) for i in found if not i["error"] and i["servers"]]
    if not usable:
        from aldwin.cli.connectors_view import import_summary

        typer.echo(import_summary(0, 0))
        raise typer.Exit(code=1)
    if not yes:
        if not _stdin_is_tty():
            typer.echo(IMPORT_NEEDS_YES, err=True)
            raise typer.Exit(code=1)
        reply = typer.prompt("Import these? [y/N]", default="", show_default=False)
        if reply.strip().lower() not in ("y", "yes"):
            typer.echo("Nothing imported.")
            raise typer.Exit(code=1)
    return usable


def _print_import_table(results: list[dict[str, Any]]) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    from aldwin.client import sanitize_display_text

    if not results:
        return
    several = len(results) > 1
    columns = ["name", "type", "status", *(["from"] if several else [])]
    table = Table(*columns, box=None, pad_edge=False)
    for result in results:
        for row in result["rows"]:
            cells = [
                row["name"],
                row["kind"],
                row["detail"],
                *([result["app"]] if several else []),
            ]
            # Text, not str: names and details come from the file and must
            # never be read as Rich markup.
            table.add_row(*(Text(sanitize_display_text(c)) for c in cells))
    Console(highlight=False).print(table)


@connectors_app.command("disable")
def disable_cmd(name: str = typer.Argument(...)) -> None:
    """Turn a connector off."""
    from aldwin.client import connectors_set_enabled

    _run(lambda: connectors_set_enabled(name, False))
    typer.echo(f"{name} disabled.")


@connectors_app.command("login")
def login_cmd(
    name: str = typer.Argument(...),
    paste: bool = typer.Option(
        False, "--paste", help="Paste the address instead of opening a browser."
    ),
) -> None:
    """Sign a connector in through your browser."""
    from aldwin.client import connectors_login_command

    async def read_line(prompt: str) -> str:
        return await asyncio.to_thread(input, prompt)

    # Over SSH a browser would open on the far machine, if anywhere.
    no_browser = paste or bool(os.environ.get("SSH_CONNECTION"))
    row = _run(
        lambda: asyncio.run(
            connectors_login_command(
                name,
                paste=no_browser,
                show=typer.echo,
                read_line=read_line,
                open_browser=webbrowser.open,
            )
        )
    )
    if row["tools_count"] is None:
        typer.echo(f"{row['title']} doesn't need sign-in, so it connected without it.")
        return
    typer.echo(f"{row['title']} signed in: {row['tools_count']} tools.")


@connectors_app.command("logout")
def logout_cmd(name: str = typer.Argument(...)) -> None:
    """Delete a connector's sign-in on this computer."""
    from aldwin.client import connectors_logout_command

    _run(lambda: connectors_logout_command(name))
    typer.echo(f"{name} signed out.")
