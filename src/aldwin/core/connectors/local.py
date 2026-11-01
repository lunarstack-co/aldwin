"""Local (stdio) connectors: the command line, the child's environment and folder,
the launch digest, and the approval shown before the program first runs.

Pure helpers: nothing here starts a process.
"""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from aldwin.core.connectors.config import WORKSPACE_CWD, ConnectorConfig
from aldwin.core.connectors.credentials import CredentialStore
from aldwin.core.connectors.errors import (
    ConnectorCommandNotFound,
    ConnectorConfigError,
)
from aldwin.core.policy import (
    ApprovalRequest,
    clean_env,
    compute_digest,
    is_blocked_path,
    match_blocked_command_path,
    match_hardline,
    normalise_command,
    sanitize_display_text,
)
from aldwin.core.workspace import (
    WorkspaceError,
    is_path_within,
    validate_workspace_root,
)

ADD_TOOL: Final[str] = "connectors.add"
ENABLE_TOOL: Final[str] = "connectors.enable"
RUNS_A_PROGRAM: Final[str] = "runs a program on this computer"
UNPINNED: Final[str] = "package version not pinned"
OUTSIDE_WORKSPACE: Final[str] = "folder outside the workspace"
CHANGES_LOADING: Final[str] = "changes how programs load"
ENV_KIND: Final[str] = "env"

_SHELL_PUNCTUATION: Final[str] = "|&;<>"
_LOADER_VARS: Final[frozenset[str]] = frozenset({"LD_PRELOAD", "LD_LIBRARY_PATH"})
_NODE_RUNNERS: Final[frozenset[str]] = frozenset({"npx", "bunx"})
_DLX_RUNNERS: Final[frozenset[str]] = frozenset({"pnpm", "yarn"})


@dataclass(frozen=True)
class LaunchSpec:
    """Exactly how a local server is started."""

    name: str
    command: str
    args: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    stored_names: frozenset[str] = frozenset()  # names whose value came from the store
    missing_names: tuple[str, ...] = ()  # allowlisted names with no value anywhere

    @property
    def argv(self) -> list[str]:
        return [self.command, *self.args]

    @property
    def env_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.env))


def parse_command_line(line: str) -> tuple[str, list[str]]:
    """Split a command line the way a shell would quote it, but refuse shell
    operators: local commands never run through a shell.
    """
    lexer = shlex.shlex(line, posix=True, punctuation_chars=_SHELL_PUNCTUATION)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as exc:
        raise ConnectorConfigError(f"That command can't be read: {exc}.") from None
    for token in tokens:
        if token and all(ch in _SHELL_PUNCTUATION for ch in token):
            raise ConnectorConfigError(
                f'Local commands run without a shell; remove "{token}".'
            )
    if not tokens:
        raise ConnectorConfigError("Enter the command that starts the server.")
    return tokens[0], tokens[1:]


def check_command_floor(argv: Sequence[str]) -> None:
    """Refuse what `run_command` refuses, with the same words, before any approval."""
    try:
        normalised = normalise_command(shlex.join(argv))
    except ValueError as exc:
        raise ConnectorConfigError(f"That command can't be run: {exc}.") from None
    rule = match_hardline(normalised)
    if rule is not None:
        raise ConnectorConfigError(f"blocked command: {rule}")
    token = match_blocked_command_path(normalised)
    if token is not None:
        raise ConnectorConfigError(f"blocked: {token} may contain secrets")


def stored_env(credentials: CredentialStore, name: str) -> dict[str, str]:
    """Values the importer saved for this connector, keyed by variable name."""
    record = credentials.get(name) or {}
    if record.get("kind") != ENV_KIND:
        return {}
    values = record.get("env") or {}
    return {str(k): str(v) for k, v in values.items()}


def build_server_env(
    config: ConnectorConfig,
    *,
    environ: Mapping[str, str],
    stored: Mapping[str, str],
    cwd: Path,
) -> dict[str, str]:
    """The child's whole environment: the safe baseline, then each allowlisted name
    from Aldwin's environment, then saved values, which win.

    Allowlisted names skip `clean_env`'s secret filter on purpose: the user named
    them, so a `MY_TOKEN` they listed must reach the server.
    """
    env = clean_env(environ, cwd=cwd)
    for name in config.env_allow:
        if name in environ:
            env[name] = environ[name]
        if name in stored:
            env[name] = stored[name]
    return env


def resolve_server_cwd(
    config: ConnectorConfig, *, workspace_root: Path, home: Path
) -> Path:
    """The folder the server runs in: the workspace, or a folder the user typed
    that is neither too broad nor one that may hold secrets.
    """
    if config.cwd == WORKSPACE_CWD:
        return workspace_root
    raw = config.cwd
    try:
        resolved = validate_workspace_root(raw, home=home)
    except WorkspaceError as exc:
        raise ConnectorConfigError(
            f"{config.name}: can't run in {raw}: {exc}"
        ) from None
    if is_blocked_path(resolved, raw_path=raw, aldwin_home=home):
        raise ConnectorConfigError(
            f"{config.name}: can't run in {raw}: may contain secrets"
        )
    return resolved


def launch_spec(
    config: ConnectorConfig,
    *,
    workspace_root: Path,
    home: Path,
    environ: Mapping[str, str],
    credentials: CredentialStore,
) -> LaunchSpec:
    """Everything needed to start `config`, checked against the command floor."""
    if config.command is None:
        raise ConnectorConfigError(f"{config.display_title} is not a local command")
    argv = [config.command, *config.args]
    check_command_floor(argv)
    cwd = resolve_server_cwd(config, workspace_root=workspace_root, home=home)
    stored = stored_env(credentials, config.name)
    env = build_server_env(config, environ=environ, stored=stored, cwd=cwd)
    return LaunchSpec(
        name=config.name,
        command=config.command,
        args=tuple(config.args),
        cwd=cwd,
        env=env,
        stored_names=frozenset(n for n in config.env_allow if n in stored),
        missing_names=tuple(n for n in config.env_allow if n not in env),
    )


def require_command(spec: LaunchSpec) -> None:
    """Raise `ConnectorCommandNotFound` unless the program can be run, so a
    missing `npx` is reported before anyone is asked anything.
    """
    command = spec.command
    if "/" in command:
        path = Path(command)
        target = path if path.is_absolute() else spec.cwd / path
        if not (target.is_file() and os.access(target, os.X_OK)):
            raise ConnectorCommandNotFound(command)
        return
    if shutil.which(command, path=spec.env.get("PATH")) is None:
        raise ConnectorCommandNotFound(command)


def launch_digest(config: ConnectorConfig) -> str:
    """What the user approved: the command, its arguments, the folder setting as
    written, and the allowlisted names. Values never count.
    """
    return compute_digest(
        ADD_TOOL,
        {
            "command": config.command,
            "args": list(config.args),
            "cwd": config.cwd,
            "env_allow": sorted(config.env_allow),
        },
        None,
    )


def is_approved(config: ConnectorConfig) -> bool:
    return config.approved_digest == launch_digest(config)


def unpinned_package(argv: Sequence[str]) -> bool:
    """True when a package runner fetches a package without a fixed version."""
    if not argv:
        return False
    program = Path(argv[0]).name
    rest = list(argv[1:])
    if program in _DLX_RUNNERS and rest[:1] == ["dlx"]:
        program, rest = "npx", rest[1:]
    if program in _NODE_RUNNERS:
        package = _option_value(rest, "-p", "--package") or _first_operand(
            rest, takes_value={"-p", "--package"}
        )
        return package is not None and not _node_pinned(package)
    if program == "uvx":
        if "--from" in rest:
            index = rest.index("--from")
            source = rest[index + 1] if index + 1 < len(rest) else ""
            return not _python_pinned(source)
        package = _first_operand(rest, takes_value={"--with", "--python"})
        return package is not None and not _python_pinned(package)
    return False


def _option_value(args: Sequence[str], short: str, long: str) -> str | None:
    """The package an `npx -p pkg cmd` call installs, which is what runs."""
    for index, arg in enumerate(args):
        if arg.startswith(f"{long}="):
            return arg.split("=", 1)[1]
        if arg in (short, long) and index + 1 < len(args):
            return args[index + 1]
    return None


def _first_operand(args: Sequence[str], *, takes_value: set[str]) -> str | None:
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in takes_value:
            skip = True
            continue
        if not arg.startswith("-"):
            return arg
    return None


def _node_pinned(package: str) -> bool:
    # A scoped package starts with "@", so its version is the second "@".
    body = package[1:] if package.startswith("@") else package
    return _exact_version(body.partition("@")[2])


def _python_pinned(package: str) -> bool:
    return "==" in package or _exact_version(package.partition("@")[2])


def _exact_version(version: str) -> bool:
    # A tag such as `latest` or a range such as `^1.2` can change under the user.
    return version[:1].isdigit()


def approval_labels(spec: LaunchSpec, *, workspace_root: Path) -> tuple[str, ...]:
    labels = [RUNS_A_PROGRAM]
    if unpinned_package(spec.argv):
        labels.append(UNPINNED)
    if not is_path_within(spec.cwd, workspace_root):
        labels.append(OUTSIDE_WORKSPACE)
    if any(n in _LOADER_VARS or n.startswith("DYLD_") for n in spec.env):
        labels.append(CHANGES_LOADING)
    return tuple(labels)


def approval_detail(spec: LaunchSpec) -> str:
    """The panel text: command, folder and variable names. Never values."""
    names = [f"{n} (saved)" if n in spec.stored_names else n for n in spec.env_names]
    names.extend(f"{n} (not set)" for n in spec.missing_names)
    lines = (
        f"Command: {shlex.join(spec.argv)}",
        f"Folder: {spec.cwd}",
        f"Environment: {', '.join(names) or 'none'}",
    )
    return "\n".join(sanitize_display_text(line) for line in lines)


def local_approval_request(
    spec: LaunchSpec,
    config: ConnectorConfig,
    *,
    tool: str,
    workspace_root: Path,
) -> ApprovalRequest:
    """The question asked before a local server first runs. Never grantable."""
    return ApprovalRequest(
        tool=tool,
        tier="ask",
        summary=f"Run a local connector: {config.name}",
        detail=approval_detail(spec),
        grantable=False,
        grant_key=None,
        risky=approval_labels(spec, workspace_root=workspace_root),
        digest=launch_digest(config),
        connector=config.name,
    )
