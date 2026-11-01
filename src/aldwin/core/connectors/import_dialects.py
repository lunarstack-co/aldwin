"""Turn one app's server entry into the shape the importer plans from.

Every app spells a server a little differently. Each dialect here reads one
spelling and fills a `ServerSpec`; the importer then applies the same rules to
all of them: secrets to the store, local commands disabled, nothing started.
Settings that only mean something to the source app (trust flags, tool lists,
timeouts) are dropped on purpose, because Aldwin's own policy applies.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from aldwin.core.connectors.errors import ConnectorConfigError

Dialect = Literal["standard", "gemini", "opencode", "codex", "zed", "goose"]

_URL_KEYS: Final[tuple[str, ...]] = ("url", "serverUrl", "httpUrl", "baseUrl")
_LOCAL_TYPES: Final[frozenset[str]] = frozenset({"stdio", "local"})
_REMOTE_TYPES: Final[frozenset[str]] = frozenset(
    {"http", "streamable-http", "streamableHttp", "remote", "sse"}
)
GOOSE_BUILTIN: Final[str] = "built into Goose"


@dataclass
class ServerSpec:
    """One server as the importer sees it, whatever app it came from."""

    kind: Literal["local", "remote"]
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, Any] = field(default_factory=dict)
    env_names: list[str] = field(default_factory=list)  # allowed names with no value
    cwd: Any = None
    url: str | None = None
    sse: bool = False
    headers: dict[str, Any] = field(default_factory=dict)
    token_env: str | None = None
    enabled: bool = True
    notes: list[str] = field(default_factory=list)


class SkipServer(Exception):
    """This entry isn't something Aldwin can import; the row says why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def to_spec(dialect: Dialect, entry: Mapping[str, Any]) -> ServerSpec:
    """Read `entry` in `dialect`. Raises `ConnectorConfigError` (`failed: …`)
    for an entry that can't be used and `SkipServer` for one to leave out.
    """
    return _DIALECTS[dialect](entry)


def _fail(reason: str) -> ConnectorConfigError:
    return ConnectorConfigError(f"failed: {reason}")


def _url_of(entry: Mapping[str, Any], keys: tuple[str, ...] = _URL_KEYS) -> str | None:
    return next((entry[k] for k in keys if isinstance(entry.get(k), str)), None)


def _disabled_flag(entry: Mapping[str, Any]) -> bool:
    return entry.get("disabled") is True or entry.get("isActive") is False


def _command_and_args(entry: Mapping[str, Any]) -> tuple[str, list[str]]:
    """`command` as a string with `args`, or as a list holding both."""
    command = entry.get("command")
    args = entry.get("args") or []
    if (
        isinstance(command, list)
        and command
        and all(isinstance(p, str) for p in command)
    ):
        command, args = command[0], [*command[1:], *args]
    if not isinstance(command, str) or not command.strip():
        raise _fail("command must be a string")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise _fail("args must be a list of strings")
    return command, list(args)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _names(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _standard(entry: Mapping[str, Any]) -> ServerSpec:
    kind = entry.get("type")
    if kind is not None and kind not in _LOCAL_TYPES | _REMOTE_TYPES:
        raise _fail(f"type {str(kind)[:40]} is not supported")
    if "command" in entry and (kind is None or kind in _LOCAL_TYPES):
        command, args = _command_and_args(entry)
        spec = ServerSpec(
            "local",
            command=command,
            args=args,
            env=_mapping(entry.get("env")),
            cwd=entry.get("cwd"),
            enabled=not _disabled_flag(entry),
        )
        if "envFile" in entry:
            spec.notes.append("skipped envFile")
        return spec
    url = _url_of(entry)
    if url is None:
        raise _fail("no command or url")
    return ServerSpec(
        "remote",
        url=url,
        sse=kind == "sse" or entry.get("transport") == "sse",
        headers=_mapping(entry.get("headers")),
        enabled=not _disabled_flag(entry),
    )


def _gemini(entry: Mapping[str, Any]) -> ServerSpec:
    # Gemini reads `url` as SSE and `httpUrl` as streamable HTTP; `trust` is
    # Gemini's own approval skip and never carries over.
    if "command" in entry:
        return _standard(entry)
    http_url = entry.get("httpUrl")
    if isinstance(http_url, str):
        spec = _standard({**entry, "url": http_url, "type": "http"})
    else:
        spec = _standard({**entry, "type": "sse"})
    return spec


def _opencode(entry: Mapping[str, Any]) -> ServerSpec:
    kind = entry.get("type")
    enabled = entry.get("enabled") is not False
    if kind == "local":
        command, args = _command_and_args(entry)
        return ServerSpec(
            "local",
            command=command,
            args=args,
            env=_mapping(entry.get("environment")),
            enabled=enabled,
        )
    if kind == "remote":
        url = entry.get("url")
        if not isinstance(url, str):
            raise _fail("no url")
        return ServerSpec(
            "remote", url=url, headers=_mapping(entry.get("headers")), enabled=enabled
        )
    raise _fail(f"type {str(kind)[:40]} is not supported")


def _codex(entry: Mapping[str, Any]) -> ServerSpec:
    enabled = entry.get("enabled") is not False
    if "command" in entry:
        command, args = _command_and_args(entry)
        return ServerSpec(
            "local",
            command=command,
            args=args,
            env=_mapping(entry.get("env")),
            env_names=_names(entry.get("env_vars")),
            cwd=entry.get("cwd"),
            enabled=enabled,
        )
    url = entry.get("url")
    if not isinstance(url, str):
        raise _fail("no command or url")
    spec = ServerSpec(
        "remote", url=url, headers=_mapping(entry.get("http_headers")), enabled=enabled
    )
    token_env = entry.get("bearer_token_env_var")
    if isinstance(token_env, str) and token_env:
        spec.token_env = token_env
    # env_http_headers maps a header to the variable that holds its value.
    for header, variable in _mapping(entry.get("env_http_headers")).items():
        if str(header).lower() == "authorization" and spec.token_env is None:
            spec.token_env = str(variable)
        else:
            spec.notes.append(f"skipped header {header}")
    return spec


def _zed(entry: Mapping[str, Any]) -> ServerSpec:
    command = entry.get("command")
    if isinstance(command, Mapping):
        # Zed's older spelling: command: {path, args, env}.
        flat = {
            "command": command.get("path"),
            "args": command.get("args"),
            "env": command.get("env"),
        }
        return _standard(flat)
    if command is not None:
        return _standard(
            {k: entry[k] for k in ("command", "args", "env") if k in entry}
        )
    url = entry.get("url")
    if not isinstance(url, str):
        raise _fail("no command or url")
    return ServerSpec("remote", url=url, headers=_mapping(entry.get("headers")))


def _goose(entry: Mapping[str, Any]) -> ServerSpec:
    kind = entry.get("type")
    if kind in ("builtin", "platform"):
        raise SkipServer(GOOSE_BUILTIN)
    enabled = entry.get("enabled") is not False
    if kind == "stdio":
        command, args = _command_and_args(
            {"command": entry.get("cmd"), "args": entry.get("args")}
        )
        return ServerSpec(
            "local",
            command=command,
            args=args,
            env=_mapping(entry.get("envs")),
            env_names=_names(entry.get("env_keys")),
            enabled=enabled,
        )
    if kind == "streamable_http":
        uri = entry.get("uri")
        if not isinstance(uri, str):
            raise _fail("no uri")
        return ServerSpec(
            "remote",
            url=uri,
            headers=_mapping(entry.get("headers")),
            env=_mapping(entry.get("envs")),
            env_names=_names(entry.get("env_keys")),
            enabled=enabled,
        )
    raise _fail(f"type {str(kind)[:40]} is not supported")


_DIALECTS: Final[dict[str, Any]] = {
    "standard": _standard,
    "gemini": _gemini,
    "opencode": _opencode,
    "codex": _codex,
    "zed": _zed,
    "goose": _goose,
}
