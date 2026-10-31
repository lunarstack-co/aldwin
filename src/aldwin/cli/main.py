"""Typer command-line entry point for Aldwin (`aldwin.cli.main:app`)."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

import typer

from aldwin import __version__, format_version_string

if TYPE_CHECKING:
    from aldwin.client import BootstrapResult

app = typer.Typer(
    name="aldwin",
    help="Aldwin: a personal assistant that runs on your computer.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    add_completion=False,
)


def _load_client():
    """Import the client only when a command needs the host.

    `--version` must stay under half a second, so the module body does not
    import the policy engine, pathspec, or tzlocal.
    """
    import aldwin.client as client

    return client


def _is_interactive_tty() -> bool:
    """Return `True` when `sys.stdin` is attached to an interactive terminal."""
    return sys.stdin.isatty()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(format_version_string())
        raise typer.Exit(code=0)


async def _launch_interactive_session(
    result: BootstrapResult,
    *,
    debug: bool = False,
) -> int:
    """Run first-run onboarding if needed, then enter the interactive chat loop."""
    from aldwin.cli.chat_screen import run_chat
    from aldwin.cli.onboarding import needs_onboarding, run_onboarding
    from aldwin.cli.theme import create_console
    from aldwin.client import get_onboarding_status

    console = create_console(plain=result.plain, theme=result.theme)
    had_env_key = get_onboarding_status(result.home).openai_source == "env"
    skip_welcome = False
    if needs_onboarding(result):
        ok = await run_onboarding(result, console)
        if not ok:
            return 1
        skip_welcome = (
            not had_env_key
            or get_onboarding_status(result.home).openai_source == "file"
        )
    elif result.created:
        console.print(f"Created {result.display_home}", soft_wrap=True)

    return await run_chat(
        result,
        debug=debug,
        skip_welcome=skip_welcome,
        console=console,
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the Aldwin version and exit.",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Disable colours and live terminal animations.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Write debug-level events to ~/.aldwin/logs/aldwin.log.",
    ),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        help="Set the workspace root directory for this session.",
    ),
) -> None:
    """Launch Aldwin interactive chat (or run a subcommand)."""
    _ = version
    if ctx.invoked_subcommand is not None:
        return

    client = _load_client()
    try:
        result = client.bootstrap(plain=plain, debug=debug, workspace=workspace)
    except client.WorkspaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from None
    except (client.PlatformNotSupportedError, client.ClientBootstrapError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    if not _is_interactive_tty():
        if result.created:
            typer.echo(f"Created {result.display_home}")
        typer.echo(
            "Aldwin requires an interactive terminal (TTY). "
            f"Aldwin {__version__} is ready (model: {result.model})."
        )
        return

    exit_code = asyncio.run(_launch_interactive_session(result, debug=debug))
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


def _add_connectors_app() -> None:
    from aldwin.cli.connectors_cmd import connectors_app

    app.add_typer(connectors_app, name="connectors")


_add_connectors_app()


@app.command("skills")
def skills_cmd() -> None:
    """List default and user skills with source labels."""
    from aldwin.cli.commands import render_skills_report
    from aldwin.cli.theme import create_console
    from aldwin.client import get_skills_report

    client = _load_client()
    try:
        result = client.bootstrap(plain=True, debug=False)
    except (client.PlatformNotSupportedError, client.ClientBootstrapError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    report = get_skills_report(result.home)
    console = create_console(plain=True)
    render_skills_report(report, console)


@app.command()
def setup(
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Disable colours and spinners during setup.",
    ),
) -> None:
    """Configure API keys and default model."""
    client = _load_client()
    try:
        result = client.bootstrap(plain=plain, debug=False)
    except (client.PlatformNotSupportedError, client.ClientBootstrapError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    from aldwin.cli.onboarding import run_setup
    from aldwin.cli.theme import create_console

    console = create_console(plain=result.plain, theme=result.theme)
    ok = asyncio.run(run_setup(result, console))
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def uninstall(
    purge: bool = typer.Option(
        False,
        "--purge",
        help="Also delete ~/.aldwin and all saved data.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Run uv tool uninstall without an interactive confirmation.",
    ),
) -> None:
    """Uninstall Aldwin (keeps ~/.aldwin unless --purge is passed)."""
    import shutil
    import subprocess

    from aldwin.cli.install_detect import detect_install_method, uninstall_command

    client = _load_client()
    try:
        result = client.bootstrap(plain=True, debug=False)
    except (client.PlatformNotSupportedError, client.ClientBootstrapError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    if purge:
        typer.echo(
            f"Warning: --purge will permanently delete {result.display_home} "
            "(all conversations, memory, persona, and API keys)."
        )
        confirmed = typer.confirm(
            f"Are you sure you want to delete {result.display_home}?",
            default=False,
        )
        if not confirmed:
            typer.echo("Aborted. Your data was not deleted.")
            raise typer.Exit(code=1)

        expected_names = {
            result.home.name,
            result.display_home,
            str(result.home),
        }
        typed = typer.prompt(
            f"Type '{result.home.name}' or '{result.display_home}' to confirm"
        )
        if typed.strip() not in expected_names:
            typer.echo("Folder name did not match. Aborted; your data was not deleted.")
            raise typer.Exit(code=1)

        client.purge_home_data(result.home)
        typer.echo(f"Deleted {result.display_home}.")
    else:
        typer.echo(f"Your data in {result.display_home} is untouched.")

    method = detect_install_method()
    command = uninstall_command(method)
    removed = False

    if method == "uv":
        if not yes:
            if not _is_interactive_tty():
                typer.echo(
                    "Refusing to uninstall without confirmation. "
                    "Re-run with --yes, or run:\n"
                    f"  {command}",
                    err=True,
                )
                raise typer.Exit(code=1)
            if not typer.confirm(
                f"Run `{command}` to remove the Aldwin command?",
                default=False,
            ):
                typer.echo("Aborted. Aldwin was not uninstalled.")
                raise typer.Exit(code=1)

        uv_bin = shutil.which("uv")
        if uv_bin is None:
            typer.echo(
                f"`uv` was not found on PATH. Remove Aldwin with:\n  {command}",
                err=True,
            )
            raise typer.Exit(code=1)

        proc = subprocess.run(
            [uv_bin, "tool", "uninstall", "aldwin"],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            if detail:
                typer.echo(detail, err=True)
            typer.echo(
                f"Failed to uninstall Aldwin (exit {proc.returncode}).",
                err=True,
            )
            raise typer.Exit(code=proc.returncode or 1)
        removed = True
    else:
        typer.echo(f"To remove the Aldwin command, run:\n  {command}")

    if removed and not purge:
        typer.echo(
            f"Aldwin removed. Your data is still in {result.display_home} "
            f"(aldwin uninstall --purge to delete it)."
        )
    elif removed and purge:
        typer.echo("Aldwin removed.")
    elif method != "uv" and not purge:
        # Printed the installer command above; still remind about data.
        typer.echo(
            f"Your data is still in {result.display_home} "
            f"(aldwin uninstall --purge to delete it)."
        )


if __name__ == "__main__":
    app()
