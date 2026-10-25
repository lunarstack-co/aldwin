"""CLI foundation tests for aldwin.cli.main."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from importlib import metadata
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aldwin import __version__
from aldwin.cli.main import app
from aldwin.client import bootstrap, whats_new_due
from aldwin.core.home import STARTER_ASSETS, SUBDIRS, atomic_write

runner = CliRunner()


@pytest.mark.uninitialized_home
def test_cli_first_run_creates_home(aldwin_home: Path) -> None:
    """TC-01: First run creates home (0700), copies assets, prints Created once."""
    assert not aldwin_home.exists()

    result = runner.invoke(app, [])
    assert result.exit_code == 0, result.output
    assert result.output.count("Created ") == 1
    assert str(aldwin_home.resolve()) in result.output

    assert (aldwin_home.stat().st_mode & 0o777) == 0o700
    for rel_dir in SUBDIRS:
        folder = aldwin_home / rel_dir
        assert folder.is_dir()
        assert (folder.stat().st_mode & 0o777) == 0o700

    for _, dest_rel in STARTER_ASSETS:
        dest = aldwin_home / dest_rel
        assert dest.is_file()
        assert (dest.stat().st_mode & 0o777) == 0o600


@pytest.mark.uninitialized_home
def test_fresh_home_never_owes_whats_new(aldwin_home: Path) -> None:
    """A brand-new home records the current version, so launch two shows no panel."""
    first = bootstrap(plain=True)
    assert first.created is True
    assert whats_new_due(aldwin_home, __version__) is False

    second = bootstrap(plain=True)
    assert second.created is False
    assert whats_new_due(aldwin_home, __version__) is False


@pytest.mark.uninitialized_home
def test_cli_second_run_is_quiet(aldwin_home: Path) -> None:
    """TC-02: Second aldwin run does not print Created and leaves files intact."""
    first = runner.invoke(app, [])
    assert first.exit_code == 0
    assert "Created " in first.output

    second = runner.invoke(app, [])
    assert second.exit_code == 0
    assert "Created " not in second.output


def test_cli_invalid_config_exits_cleanly(aldwin_home: Path) -> None:
    """TC-06: Invalid config value names file, field, and expected type; exits 1."""
    config_path = aldwin_home / "config.yaml"
    atomic_write(config_path, 'limits:\n  max_steps: "lots"\n')

    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "config.yaml" in result.output
    assert "limits.max_steps" in result.output
    assert "integer >= 1" in result.output
    assert "Traceback" not in result.output


@pytest.mark.uninitialized_home
def test_cli_version_fast_and_side_effect_free(aldwin_home: Path) -> None:
    """TC-12: aldwin --version prints version, exits 0 in < 0.5 s, creates no home."""
    expected = (
        f"aldwin {__version__} (protocol v2, agentling {metadata.version('agentling')})"
    )
    in_proc = runner.invoke(app, ["--version"])
    assert in_proc.exit_code == 0
    assert in_proc.output.strip() == expected

    env = os.environ.copy()
    env["ALDWIN_HOME"] = str(aldwin_home)

    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "aldwin.cli.main", "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    elapsed = time.perf_counter() - start

    assert proc.returncode == 0
    assert proc.stdout.strip() == expected
    assert elapsed < 0.5, f"aldwin --version took {elapsed:.3f}s (budget < 0.5s)"
    assert not aldwin_home.exists()


def test_cli_help_lists_commands_and_options() -> None:
    """TC-28: aldwin --help lists commands and options."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    plain_output = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "--version" in plain_output
    assert "--plain" in plain_output
    assert "--debug" in plain_output
    assert "setup" in plain_output
    assert "uninstall" in plain_output


@pytest.mark.uninitialized_home
def test_cli_windows_exits_with_friendly_message(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-29: Windows platform exits 1 with friendly message and creates no home."""
    monkeypatch.setattr(sys, "platform", "win32")

    result = runner.invoke(app, [])
    assert result.exit_code == 1
    assert "Windows" in result.output
    assert "not support" in result.output
    assert not aldwin_home.exists()


def test_cli_subcommands_stub_output(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """setup and uninstall commands exit 0 cleanly when given valid input."""
    import aldwin.cli.onboarding as onboarding_mod

    async def _always_ok(_key: str) -> onboarding_mod.KeyCheckResult:
        return "ok"

    async def _models(_key: str | None, _home: Path | None) -> list[str]:
        return ["gpt-4o"]

    monkeypatch.setattr(onboarding_mod, "check_openai_key", _always_ok)
    monkeypatch.setattr(onboarding_mod, "list_available_models", _models)

    setup_res = runner.invoke(
        app,
        ["setup", "--plain"],
        input="sk-test-12345678\n\n1\n",
    )
    assert setup_res.exit_code == 0, setup_res.output

    import aldwin.cli.install_detect as detect_mod

    monkeypatch.setattr(detect_mod, "detect_install_method", lambda: "pip")
    uninstall_res = runner.invoke(
        app,
        ["uninstall", "--purge"],
        input=f"y\n{aldwin_home.name}\n",
    )
    assert uninstall_res.exit_code == 0, uninstall_res.output
    assert not aldwin_home.exists()


def test_cli_default_home_prints_tilde_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First run under default ~/.aldwin prints 'Created ~/.aldwin'."""
    fake_user_home = tmp_path / "user"
    fake_user_home.mkdir(exist_ok=True)
    monkeypatch.delenv("ALDWIN_HOME", raising=False)
    monkeypatch.setenv("HOME", str(fake_user_home))

    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Created ~/.aldwin\n" in result.output


def test_bootstrap_resolves_plain_mode_from_flag_env_and_config(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bootstrap() sets plain=True from --plain, NO_COLOR, or theme: plain."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert bootstrap(plain=False).plain is False
    assert bootstrap(plain=True).plain is True

    monkeypatch.setenv("NO_COLOR", "1")
    assert bootstrap(plain=False).plain is True

    monkeypatch.delenv("NO_COLOR", raising=False)
    atomic_write(aldwin_home / "config.yaml", "theme: plain\n")
    assert bootstrap(plain=False).plain is True
