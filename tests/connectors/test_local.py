"""Local connector helpers: command line, environment, folder, digest, approval."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.credentials import FileCredentialStore
from aldwin.core.connectors.errors import ConnectorConfigError
from aldwin.core.connectors.local import (
    ADD_TOOL,
    CHANGES_LOADING,
    OUTSIDE_WORKSPACE,
    RUNS_A_PROGRAM,
    UNPINNED,
    build_server_env,
    check_command_floor,
    launch_digest,
    launch_spec,
    local_approval_request,
    parse_command_line,
    resolve_server_cwd,
    unpinned_package,
)
from aldwin.core.workspace import Workspace

ENVIRON = {
    "PATH": "/usr/bin",
    "HOME": "/home/u",
    "OPENAI_API_KEY": "x",
    "MY_TOKEN": "y",
    "GITHUB_TOKEN": "z",
}


def _config(**overrides: Any) -> ConnectorConfig:
    return ConnectorConfig.model_validate(
        {"name": "fixture", "command": "python", "args": ["-I", "s.py"], **overrides}
    )


def _spec(
    config: ConnectorConfig,
    workspace: Workspace,
    aldwin_home: Path,
    environ: dict[str, str] | None = None,
) -> Any:
    return launch_spec(
        config,
        workspace_root=workspace.root,
        home=aldwin_home,
        environ=environ if environ is not None else ENVIRON,
        credentials=FileCredentialStore(aldwin_home),
    )


def test_build_server_env_drops_secrets_without_allowlist(tmp_path: Path) -> None:
    env = build_server_env(_config(), environ=ENVIRON, stored={}, cwd=tmp_path)
    assert not {"OPENAI_API_KEY", "MY_TOKEN", "GITHUB_TOKEN"} & set(env)
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/home/u"


def test_build_server_env_keeps_allowlisted_token(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="aldwin.core.policy")
    env = build_server_env(
        _config(env_allow=["MY_TOKEN"]), environ=ENVIRON, stored={}, cwd=tmp_path
    )
    assert env["MY_TOKEN"] == "y"
    assert "OPENAI_API_KEY" not in env and "GITHUB_TOKEN" not in env
    # The builder never goes through clean_env's allowlist, which drops secrets.
    assert not [r for r in caplog.records if r.name == "aldwin.core.policy"]


def test_stored_value_overrides_environment(tmp_path: Path) -> None:
    env = build_server_env(
        _config(env_allow=["GITHUB_TOKEN", "DATABASE_URL"]),
        environ={**ENVIRON, "GITHUB_TOKEN": "fromenv"},
        stored={
            "GITHUB_TOKEN": "stored",
            "DATABASE_URL": "postgres://u:fakepass@localhost/db",
        },
        cwd=tmp_path,
    )
    assert env["GITHUB_TOKEN"] == "stored"
    assert env["DATABASE_URL"] == "postgres://u:fakepass@localhost/db"


def test_unset_name_absent_and_marked_not_set(
    workspace: Workspace, aldwin_home: Path
) -> None:
    FileCredentialStore(aldwin_home).set(
        "fixture", {"v": 1, "kind": "env", "env": {"GITHUB_TOKEN": "stored"}}
    )
    config = _config(env_allow=["NOT_SET_VAR", "GITHUB_TOKEN"])
    spec = _spec(config, workspace, aldwin_home)
    request = local_approval_request(
        spec, config, tool=ADD_TOOL, workspace_root=workspace.root
    )
    assert "NOT_SET_VAR" not in spec.env
    assert request.detail is not None
    assert "NOT_SET_VAR (not set)" in request.detail
    assert "GITHUB_TOKEN (saved)" in request.detail
    assert "stored" not in request.detail


@pytest.mark.parametrize("op", ["|", "&&", ";", ">", "<", "&", "||"])
def test_parse_command_line_refuses_shell_operators(op: str) -> None:
    with pytest.raises(ConnectorConfigError) as info:
        parse_command_line(f"npx pkg {op} tee out")
    assert info.value.message == f'Local commands run without a shell; remove "{op}".'


def test_parse_command_line_refuses_glued_operator() -> None:
    with pytest.raises(ConnectorConfigError):
        parse_command_line("a; b")


def test_dollar_paren_stays_literal() -> None:
    assert parse_command_line("echo $(whoami)") == ("echo", ["$(whoami)"])
    assert parse_command_line('npx -y "my pkg"') == ("npx", ["-y", "my pkg"])


def test_empty_command_is_refused() -> None:
    with pytest.raises(ConnectorConfigError):
        parse_command_line("   ")


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["rm", "-rf", "~"], "blocked command: "),
        (["cat", "~/.ssh/id_rsa"], "blocked: "),
    ],
)
def test_command_floor_uses_run_command_wording(argv: list[str], expected: str) -> None:
    with pytest.raises(ConnectorConfigError) as info:
        check_command_floor(argv)
    assert info.value.message.startswith(expected)
    if expected == "blocked: ":
        assert info.value.message.endswith(" may contain secrets")


def _cwd_error(config: ConnectorConfig, workspace: Workspace, home: Path) -> str:
    with pytest.raises(ConnectorConfigError) as info:
        resolve_server_cwd(config, workspace_root=workspace.root, home=home)
    return info.value.message


def test_resolve_server_cwd_rules(
    workspace: Workspace, aldwin_home: Path, tmp_path: Path
) -> None:
    user_home = Path.home()
    (user_home / ".ssh").mkdir(exist_ok=True)
    assert (
        resolve_server_cwd(_config(), workspace_root=workspace.root, home=aldwin_home)
        == workspace.root
    )
    for raw in ("/", str(user_home), str(aldwin_home), str(user_home / ".ssh")):
        message = _cwd_error(_config(cwd=raw), workspace, aldwin_home)
        assert message.startswith(f"fixture: can't run in {raw}: ")
    if sys.platform == "darwin":
        message = _cwd_error(
            _config(cwd=str(user_home / ".SSH")), workspace, aldwin_home
        )
        assert message.endswith(": may contain secrets")

    other = tmp_path / "other"
    other.mkdir()
    config = _config(cwd=str(other))
    assert (
        resolve_server_cwd(config, workspace_root=workspace.root, home=aldwin_home)
        == other.resolve()
    )
    spec = _spec(config, workspace, aldwin_home)
    request = local_approval_request(
        spec, config, tool=ADD_TOOL, workspace_root=workspace.root
    )
    assert OUTSIDE_WORKSPACE in request.risky
    assert request.detail is not None
    assert f"Folder: {other.resolve()}" in request.detail

    inside = _spec(_config(), workspace, aldwin_home)
    inside_request = local_approval_request(
        inside, _config(), tool=ADD_TOOL, workspace_root=workspace.root
    )
    assert OUTSIDE_WORKSPACE not in inside_request.risky


@pytest.mark.parametrize("name", ["LD_PRELOAD", "DYLD_INSERT_LIBRARIES"])
def test_loader_vars_add_label(
    name: str, workspace: Workspace, aldwin_home: Path
) -> None:
    config = _config(env_allow=[name])
    spec = _spec(config, workspace, aldwin_home, {**ENVIRON, name: "/x.so"})
    request = local_approval_request(
        spec, config, tool=ADD_TOOL, workspace_root=workspace.root
    )
    assert CHANGES_LOADING in request.risky


def test_launch_digest_covers_command_args_cwd_names_only() -> None:
    base = _config(env_allow=["B", "A"])
    assert launch_digest(base) == launch_digest(_config(env_allow=["A", "B"]))
    assert launch_digest(base) == launch_digest(
        base.model_copy(update={"enabled": False, "note": "review and enable"})
    )
    assert launch_digest(base) != launch_digest(
        _config(args=["-S", "s.py"], env_allow=["A", "B"])
    )
    assert launch_digest(base) != launch_digest(
        _config(command="node", env_allow=["A", "B"])
    )
    assert launch_digest(base) != launch_digest(
        _config(cwd="/tmp", env_allow=["A", "B"])
    )
    assert launch_digest(base) != launch_digest(_config(env_allow=["A"]))


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["npx", "-y", "@modelcontextprotocol/server-everything"], True),
        (["bunx", "pkg"], True),
        (["pnpm", "dlx", "pkg"], True),
        (["uvx", "mcp-server-git"], True),
        (["npx", "-y", "@modelcontextprotocol/server-everything@2.1.0"], False),
        (["npx", "pkg@1.0.0"], False),
        (["uvx", "mcp-server-git==0.6.2"], False),
        (["uvx", "mcp-server-git@0.6.2"], False),
        (["uvx", "--from", "mcp-server-git==0.6.2", "mcp-server-git"], False),
        (["python", "-m", "x"], False),
        (["npx", "-y", "pkg@latest"], True),
        (["npx", "@scope/pkg@^1.2"], True),
        (["uvx", "mcp-server-git@latest"], True),
        (["npx", "-p", "pkg@1.0.0", "pkg-cli"], False),
        (["npx", "--package=pkg", "pkg-cli"], True),
    ],
)
def test_unpinned_package(
    argv: list[str], expected: bool, workspace: Workspace, aldwin_home: Path
) -> None:
    assert unpinned_package(argv) is expected
    config = _config(command=argv[0], args=argv[1:])
    spec = _spec(config, workspace, aldwin_home)
    request = local_approval_request(
        spec, config, tool=ADD_TOOL, workspace_root=workspace.root
    )
    assert request.risky[0] == RUNS_A_PROGRAM
    assert (UNPINNED in request.risky) is expected


def test_panel_detail_sanitised_without_values(
    workspace: Workspace, aldwin_home: Path
) -> None:
    config = _config(command="printf", args=["\x1b[31mred‮"], env_allow=["MY_TOKEN"])
    spec = _spec(config, workspace, aldwin_home, {**ENVIRON, "MY_TOKEN": "val-77"})
    request = local_approval_request(
        spec, config, tool=ADD_TOOL, workspace_root=workspace.root
    )
    assert request.detail is not None
    assert "\x1b" not in request.detail and "‮" not in request.detail
    assert "val-77" not in request.detail
    assert request.detail.splitlines()[0].startswith("Command: printf ")
    assert (request.grantable, request.tier, request.summary) == (
        False,
        "ask",
        "Run a local connector: fixture",
    )


def test_launch_spec_refuses_a_remote_entry(
    workspace: Workspace, aldwin_home: Path
) -> None:
    remote = ConnectorConfig(name="web", url="https://web.example/mcp")
    with pytest.raises(ConnectorConfigError):
        _spec(remote, workspace, aldwin_home)
