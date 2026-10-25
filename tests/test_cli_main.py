"""CliRunner tests for `aldwin`, `aldwin setup`, and `aldwin uninstall`."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import pytest
from typer.testing import CliRunner

import aldwin.cli.main as cli_main
import aldwin.cli.onboarding as onboarding_mod
from aldwin import __version__
from aldwin.cli.main import app
from aldwin.client import BootstrapResult, get_onboarding_status

runner = CliRunner()


@pytest.mark.uninitialized_home
def test_cli_main_routes_to_chat_and_version(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare `aldwin` routes to chat entry; `--version` prints version."""
    ver_res = runner.invoke(app, ["--version"])
    assert ver_res.exit_code == 0
    assert ver_res.output.strip() == (
        f"aldwin {__version__} (protocol v2, agentling {metadata.version('agentling')})"
    )
    assert not aldwin_home.exists()

    routed: list[BootstrapResult] = []

    async def fake_launch(result: BootstrapResult, *, debug: bool = False) -> int:
        _ = debug
        routed.append(result)
        return 0

    monkeypatch.setattr(cli_main, "_is_interactive_tty", lambda: True)
    monkeypatch.setattr(cli_main, "_launch_interactive_session", fake_launch)

    chat_res = runner.invoke(app, ["--plain"])
    assert chat_res.exit_code == 0, chat_res.output
    assert len(routed) == 1
    assert routed[0].home == aldwin_home.resolve()
    assert routed[0].plain is True


def test_uninstall_uv_yes_runs_subprocess(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """uv install + --yes runs `uv tool uninstall aldwin`."""
    import subprocess

    import aldwin.cli.install_detect as detect_mod

    runner.invoke(app, [])
    assert aldwin_home.exists()

    calls: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd: list[str], **_kwargs: object) -> _Proc:
        calls.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(detect_mod, "detect_install_method", lambda: "uv")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(subprocess, "run", fake_run)

    res = runner.invoke(app, ["uninstall", "--yes"])
    assert res.exit_code == 0, res.output
    assert aldwin_home.exists()
    assert calls == [["/usr/bin/uv", "tool", "uninstall", "aldwin"]]
    assert "Aldwin removed" in res.output
    assert "uninstall --purge" in res.output


def test_uninstall_uv_decline_does_not_run(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declining the uv confirm leaves the tool installed."""
    import aldwin.cli.install_detect as detect_mod

    runner.invoke(app, [])
    monkeypatch.setattr(detect_mod, "detect_install_method", lambda: "uv")
    monkeypatch.setattr(cli_main, "_is_interactive_tty", lambda: True)

    ran = False

    def boom(*_a: object, **_k: object) -> None:
        nonlocal ran
        ran = True
        raise AssertionError("subprocess should not run")

    monkeypatch.setattr("subprocess.run", boom)
    res = runner.invoke(app, ["uninstall"], input="n\n")
    assert res.exit_code == 1
    assert not ran
    assert aldwin_home.exists()
    assert "Aborted" in res.output


def test_uninstall_uv_noninteractive_requires_yes(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-TTY without --yes refuses to uninstall."""
    import aldwin.cli.install_detect as detect_mod

    runner.invoke(app, [])
    monkeypatch.setattr(detect_mod, "detect_install_method", lambda: "uv")
    monkeypatch.setattr(cli_main, "_is_interactive_tty", lambda: False)

    res = runner.invoke(app, ["uninstall"])
    assert res.exit_code == 1
    assert "--yes" in res.output
    assert aldwin_home.exists()


def test_uninstall_pipx_prints_command(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pipx install prints the command and removes nothing."""
    import aldwin.cli.install_detect as detect_mod

    runner.invoke(app, [])
    monkeypatch.setattr(detect_mod, "detect_install_method", lambda: "pipx")

    res = runner.invoke(app, ["uninstall"])
    assert res.exit_code == 0, res.output
    assert "pipx uninstall aldwin" in res.output
    assert "Aldwin removed" not in res.output
    assert aldwin_home.exists()


def test_uninstall_keeps_data_and_purge_requires_double_confirmation(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--purge deletes data after confirm; then prints/runs tool remove."""
    import subprocess

    import aldwin.cli.install_detect as detect_mod

    runner.invoke(app, [])
    assert aldwin_home.exists()
    assert (aldwin_home / "config.yaml").is_file()

    monkeypatch.setattr(detect_mod, "detect_install_method", lambda: "pip")

    decline_res = runner.invoke(app, ["uninstall", "--purge"], input="n\n")
    assert decline_res.exit_code == 1
    assert aldwin_home.exists()

    wrong_name_res = runner.invoke(
        app,
        ["uninstall", "--purge"],
        input="y\nwrong-folder-name\n",
    )
    assert wrong_name_res.exit_code == 1
    assert "did not match" in wrong_name_res.output
    assert aldwin_home.exists()

    purge_res = runner.invoke(
        app,
        ["uninstall", "--purge"],
        input=f"y\n{aldwin_home.name}\n",
    )
    assert purge_res.exit_code == 0, purge_res.output
    assert "pip uninstall aldwin" in purge_res.output
    assert not aldwin_home.exists()

    # Restore home for isolation; exercise uv failure path.
    runner.invoke(app, [])
    monkeypatch.setattr(detect_mod, "detect_install_method", lambda: "uv")
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/bin/uv")

    class _Fail:
        returncode = 2
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _Fail())
    fail_res = runner.invoke(app, ["uninstall", "--yes"])
    assert fail_res.exit_code == 2
    assert "Aldwin removed" not in fail_res.output


def test_setup_cli_command_end_to_end(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`aldwin setup` saves keys and model via CliRunner."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    async def fake_check(_key: str) -> onboarding_mod.KeyCheckResult:
        return "ok"

    async def fake_models(_key: str | None, _home: Path | None) -> list[str]:
        return ["gpt-4o", "gpt-4.1"]

    monkeypatch.setattr(onboarding_mod, "check_openai_key", fake_check)
    monkeypatch.setattr(onboarding_mod, "list_available_models", fake_models)

    res = runner.invoke(
        app,
        ["setup", "--plain"],
        input="sk-proj-setup9999\ntvly-dev-setup8888\n2\n",
    )
    assert res.exit_code == 0, res.output
    status = get_onboarding_status(aldwin_home)
    assert status.masked_openai_key == "sk-…9999"
    assert status.masked_tavily_key == "tvly-…8888"
    assert status.model == "gpt-4.1"


def test_non_tty_graceful_exit(aldwin_home: Path) -> None:
    """Non-TTY invocation exits 0 cleanly with no traceback."""
    res = runner.invoke(app, [])
    assert res.exit_code == 0
    assert "Traceback" not in res.output
    assert "interactive terminal" in res.output
    assert aldwin_home.exists()


async def test_launch_interactive_session_onboarding_and_chat(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_launch_interactive_session` runs onboarding when needed, then `run_chat`."""
    import aldwin.cli.chat_screen as chat_mod
    from aldwin.client import bootstrap, save_setup

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    # Case 1: onboarding fails -> returns exit code 1
    async def fail_onboarding(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(onboarding_mod, "run_onboarding", fail_onboarding)
    assert await cli_main._launch_interactive_session(boot, debug=False) == 1

    # Case 2: key present -> skips onboarding and runs run_chat(skip_welcome=False)
    save_setup(openai_api_key="sk-proj-ready1234", home=aldwin_home)
    seen_skip_welcome: list[bool] = []

    async def fake_run_chat(
        *_args: object,
        skip_welcome: bool = False,
        **_kwargs: object,
    ) -> int:
        seen_skip_welcome.append(skip_welcome)
        return 0

    monkeypatch.setattr(chat_mod, "run_chat", fake_run_chat)
    assert await cli_main._launch_interactive_session(boot, debug=True) == 0
    assert seen_skip_welcome == [False]

    # Case 3: onboarding runs -> passes skip_welcome=True to avoid duplicate panel
    (aldwin_home / "secrets.yaml").unlink()

    async def ok_onboarding(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(onboarding_mod, "run_onboarding", ok_onboarding)
    assert await cli_main._launch_interactive_session(boot, debug=False) == 0
    assert seen_skip_welcome == [False, True]


def test_cli_help_has_no_internal_ticket_tags() -> None:
    """`aldwin --help` describes `setup` and `uninstall` without ``."""
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "setup" in res.output
    assert "uninstall" in res.output
    assert "FR-1" not in res.output


def test_cli_workspace_option_rejects_invalid_paths_with_exit_2(
    aldwin_home: Path,
) -> None:
    """`aldwin --workspace` exits 2 on missing, `/`, `~`, or `~/.aldwin`."""
    res_missing = runner.invoke(app, ["--workspace", "/nope"])
    assert res_missing.exit_code == 2
    assert "workspace not found: /nope" in res_missing.output

    res_root = runner.invoke(app, ["--workspace", "/"])
    assert res_root.exit_code == 2
    assert "workspace too broad: /" in res_root.output

    res_tilde = runner.invoke(app, ["--workspace", "~"])
    assert res_tilde.exit_code == 2
    assert "workspace too broad: ~" in res_tilde.output

    res_home = runner.invoke(app, ["--workspace", str(aldwin_home)])
    assert res_home.exit_code == 2
    assert "workspace not allowed:" in res_home.output
