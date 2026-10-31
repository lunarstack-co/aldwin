"""`aldwin connectors …` shell commands (`FR-27`).

Exit codes: 0 on success, 1 on a user error (the message is printed), 2 on
anything unexpected.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, TypeVar

import typer

connectors_app = typer.Typer(
    name="connectors",
    help="Add and manage connected apps (MCP servers).",
    no_args_is_help=True,
    add_completion=False,
)

_T = TypeVar("_T")


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
        typer.echo(f"{row['name']}  {row['url']}  ({state})")


@connectors_app.command("add")
def add_cmd(
    name: str = typer.Argument(..., help="Short name, such as github."),
    url: str = typer.Option(..., "--url", help="The server's MCP URL."),
    token_env: str | None = typer.Option(
        None, "--token-env", help="Environment variable that holds the token."
    ),
) -> None:
    """Check a server and save it as a connector."""
    from aldwin.client import connectors_add_command

    row = _run(
        lambda: asyncio.run(connectors_add_command(name, url, token_env=token_env))
    )
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
def enable_cmd(name: str = typer.Argument(...)) -> None:
    """Turn a connector on."""
    from aldwin.client import connectors_set_enabled

    _run(lambda: connectors_set_enabled(name, True))
    typer.echo(f"{name} enabled.")


@connectors_app.command("disable")
def disable_cmd(name: str = typer.Argument(...)) -> None:
    """Turn a connector off."""
    from aldwin.client import connectors_set_enabled

    _run(lambda: connectors_set_enabled(name, False))
    typer.echo(f"{name} disabled.")
