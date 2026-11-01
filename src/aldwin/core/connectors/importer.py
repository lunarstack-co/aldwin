"""Import MCP servers from the config files of other agent apps.

Reading and planning never start anything. Applying writes every secret value
to the credential store first, then `connectors.yaml` once, so the yaml never
names a secret that was not saved. Imported local commands start disabled and
ask before they first run; so does everything from a file that came with a
project, because its author may not be the user.

Which apps and files exist is data (`import_sources.py`); how each app spells a
server is a dialect (`import_dialects.py`). This module reads the files, maps
each server through its dialect, and decides what to save.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import yaml

from aldwin.core.connectors.config import (
    ENV_NAME_PATTERN,
    ConnectorConfig,
    check_url,
    is_valid_name,
    load_connectors,
    save_connectors,
)
from aldwin.core.connectors.credentials import (
    CREDENTIAL_VERSION,
    CredentialStore,
    bearer_credential,
)
from aldwin.core.connectors.errors import ConnectorConfigError, ConnectorError
from aldwin.core.connectors.import_dialects import (
    Dialect,
    ServerSpec,
    SkipServer,
    to_spec,
)
from aldwin.core.connectors.import_sources import (
    DEFAULT_MAX_BYTES,
    WORKSPACE_TOKEN,
    Format,
    ImportSource,
    Scope,
    load_import_sources,
    match_source,
    resolve_locations,
)
from aldwin.core.connectors.local import ENV_KIND

logger = logging.getLogger("aldwin.core.connectors.importer")

MAX_IMPORT_BYTES: Final[int] = DEFAULT_MAX_BYTES
REVIEW_NOTE: Final[str] = "review and enable"
SSE_FAILED: Final[str] = "failed: SSE is not supported"
NAME_TAKEN: Final[str] = "skipped: name already exists"
CUSTOM_APP: Final[str] = "Custom"

_ENV_NAME_RE = re.compile(ENV_NAME_PATTERN)
# A whole value that names a variable: $VAR, ${VAR}, ${env:VAR}, {env:VAR},
# ${VAR:-default}. Anything else is a literal, so `pa$$word` stays a password.
_REFERENCE_RE = re.compile(
    r"\A(?:\$([A-Za-z_]\w*)"
    r"|\$\{(?:env:)?([A-Za-z_]\w*)(?::-(.*))?\}"
    r"|\{env:([A-Za-z_]\w*)\})\Z",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"\A\s*bearer\s+(.+?)\s*\Z", re.IGNORECASE | re.DOTALL)
_FORMAT_LABEL: Final[dict[str, str]] = {
    "json": "JSON",
    "jsonc": "JSON",
    "toml": "TOML",
    "yaml": "YAML",
}

ImportKind = Literal["local", "remote"]
ImportStatus = Literal["imported", "skipped", "failed"]


@dataclass(frozen=True)
class ImportRow:
    """What happened to one server in the file."""

    source_name: str
    name: str
    kind: ImportKind
    status: ImportStatus
    detail: str


@dataclass(frozen=True)
class _Planned:
    config: ConnectorConfig
    secret: dict[str, Any] | None
    values: int


@dataclass(frozen=True)
class ImportPlan:
    rows: tuple[ImportRow, ...]
    entries: tuple[_Planned, ...] = field(repr=False)


@dataclass(frozen=True)
class ImportResult:
    rows: tuple[ImportRow, ...]
    imported: int  # rows saved, SSE entries included
    values: int  # secret values moved to the credential store
    app: str = CUSTOM_APP
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadedFile:
    """One config file, read and reduced to its server entries."""

    path: Path
    app: str
    scope: Scope
    dialect: Dialect
    servers: tuple[tuple[str, Mapping[str, Any]], ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FoundConfig:
    """A config file another app keeps MCP servers in."""

    app: str
    scope: Scope
    path: Path
    servers: int
    error: str | None = None


@dataclass(frozen=True)
class Reference:
    """A value that names an environment variable instead of holding one."""

    name: str
    default: str | None = None


# --- finding files -------------------------------------------------------------


def find_config_files(
    *,
    user_home: Path,
    workspace: Path | None,
    sources: Sequence[ImportSource] | None = None,
    extra_paths: Iterable[str] = (),
    platform: str = sys.platform,
) -> tuple[list[FoundConfig], list[str]]:
    """The config files that exist, each with its server count, plus warnings.

    Reads only. A missing file is the normal case and says nothing; anything
    else that goes wrong becomes one warning and never stops the search.
    """
    warnings: list[str] = []
    if sources is None:
        sources, source_warnings = load_import_sources()
        warnings.extend(source_warnings)
    found: list[FoundConfig] = []
    seen: set[Path] = set()
    for source in sources:
        locations, location_warnings = resolve_locations(
            source, user_home=user_home, workspace=workspace, platform=platform
        )
        warnings.extend(location_warnings)
        for location in locations:
            if not _is_file(location.path) or _resolved(location.path) in seen:
                continue
            seen.add(_resolved(location.path))
            found.append(
                _inspect(
                    location.path,
                    source,
                    location.scope,
                    workspace,
                    warnings,
                    user_home=user_home,
                    sources=sources,
                )
            )
    for raw in extra_paths:
        path = Path(raw).expanduser()
        if not _is_file(path):
            warnings.append(f"import path {raw}: no such file")
            continue
        if _resolved(path) in seen:
            continue
        seen.add(_resolved(path))
        found.append(
            _inspect(
                path,
                None,
                "user",
                workspace,
                warnings,
                user_home=user_home,
                sources=sources,
            )
        )
    return found, warnings


def _inspect(
    path: Path,
    source: ImportSource | None,
    scope: Scope,
    workspace: Path | None,
    warnings: list[str],
    *,
    user_home: Path,
    sources: Sequence[ImportSource],
) -> FoundConfig:
    app = source.app if source is not None else CUSTOM_APP
    try:
        loaded = (
            _load_known(path, source, scope, workspace)
            if source is not None
            else load_file(
                path, user_home=user_home, workspace=workspace, sources=sources
            )
        )
    except ConnectorConfigError as exc:
        return FoundConfig(app, scope, path, 0, exc.message)
    except Exception as exc:  # one unreadable file must not stop the search
        logger.exception("import: couldn't read %s", path)
        warnings.append(f"{app}: couldn't read {path} ({type(exc).__name__})")
        return FoundConfig(
            app, scope, path, 0, f"couldn't read it ({type(exc).__name__})"
        )
    warnings.extend(loaded.warnings)
    return FoundConfig(loaded.app, loaded.scope, path, len(loaded.servers))


# --- reading one file --------------------------------------------------------


def load_file(
    path: Path,
    *,
    user_home: Path,
    workspace: Path | None,
    sources: Sequence[ImportSource] | None = None,
    platform: str = sys.platform,
) -> LoadedFile:
    """Read `path`: as its app's file when the path is a known location,
    otherwise by what the file contains.
    """
    if sources is None:
        sources, _ = load_import_sources()
    known = match_source(
        path, sources, user_home=user_home, workspace=workspace, platform=platform
    )
    if known is not None:
        source, scope = known
        return _load_known(path, source, scope, workspace)
    return _load_detected(path, workspace)


def _load_known(
    path: Path, source: ImportSource, scope: Scope, workspace: Path | None
) -> LoadedFile:
    raw = read_source_file(path, fmt=source.format, max_bytes=source.max_bytes)
    servers, warnings = _servers_at(raw, source.servers_at, workspace)
    return LoadedFile(path, source.app, scope, source.dialect, servers, tuple(warnings))


def _load_detected(path: Path, workspace: Path | None) -> LoadedFile:
    fmt = _format_for(path)
    raw = read_source_file(path, fmt=fmt, max_bytes=DEFAULT_MAX_BYTES)
    detected = detect_layout(raw, fmt)
    if detected is None:
        raise ConnectorConfigError(f"No MCP servers found in {path}.")
    dialect, servers_at = detected
    servers, warnings = _servers_at(raw, servers_at, workspace)
    return LoadedFile(path, CUSTOM_APP, "user", dialect, servers, tuple(warnings))


def detect_layout(raw: Any, fmt: Format) -> tuple[Dialect, list[list[str]]] | None:
    """Which dialect and key path a file of unknown origin uses, if any."""
    if not isinstance(raw, Mapping):
        return None
    if fmt == "toml":
        return ("codex", [["mcp_servers"]]) if _is_map(raw.get("mcp_servers")) else None
    if fmt == "yaml":
        if _is_map(raw.get("extensions")):
            return "goose", [["extensions"]]
        if _is_map(raw.get("mcp_servers")):
            return "standard", [["mcp_servers"]]
        return None
    for key in ("mcpServers", "servers"):
        if _is_map(raw.get(key)):
            return "standard", [[key]]
    tools = raw.get("tools")
    if isinstance(tools, Mapping) and any(
        _is_map(tools.get(k)) for k in ("mcp_servers", "mcpServers")
    ):
        return "standard", [["tools", "mcp_servers"], ["tools", "mcpServers"]]
    if _is_map(raw.get("mcp")):
        return "opencode", [["mcp"]]
    if _is_map(raw.get("context_servers")):
        return "zed", [["context_servers"]]
    return None


def read_source_file(path: Path, *, fmt: Format, max_bytes: int) -> Any:
    """Parse `path` as `fmt`, refusing folders, oversize and unreadable files."""
    label = _FORMAT_LABEL[fmt]
    try:
        if path.is_dir():
            raise _Unreadable("it is a folder")
        if path.stat().st_size > max_bytes:
            raise _Unreadable(f"it is larger than {_mb(max_bytes)}")
        text = path.read_bytes().decode("utf-8")
    except _Unreadable as exc:
        raise _unreadable(path, label, str(exc)) from None
    except FileNotFoundError:
        raise _unreadable(path, label, "no such file") from None
    except UnicodeDecodeError:
        raise _unreadable(path, label, "it isn't UTF-8 text") from None
    except OSError as exc:
        raise _unreadable(path, label, exc.strerror or "it can't be opened") from None
    try:
        return _parse(text, fmt)
    except RecursionError:
        raise _unreadable(path, label, "it is nested too deeply") from None
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, yaml.YAMLError) as exc:
        raise _unreadable(path, label, _parse_reason(exc)) from None


def read_config_file(path: Path) -> dict[str, Any]:
    """Read a standard JSON or JSONC file that lists MCP servers."""
    raw = read_source_file(path, fmt="json", max_bytes=DEFAULT_MAX_BYTES)
    if detect_layout(raw, "json") is None:
        raise ConnectorConfigError(f"No MCP servers found in {path}.")
    return dict(raw)


def _parse(text: str, fmt: Format) -> Any:
    if fmt == "toml":
        return tomllib.loads(text)
    if fmt == "yaml":
        return yaml.safe_load(text)
    if fmt == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass  # many "JSON" files carry comments or trailing commas
    return json.loads(strip_jsonc(text))


def _servers_at(
    raw: Any, key_paths: Sequence[Sequence[str]], workspace: Path | None
) -> tuple[tuple[tuple[str, Mapping[str, Any]], ...], list[str]]:
    """The server entries under any of `key_paths`.

    A missing key is the normal state of a settings file with no servers in
    it, so it is reported as a count of zero, not as a warning.
    """
    servers: list[tuple[str, Mapping[str, Any]]] = []
    for key_path in key_paths:
        block = _descend(raw, key_path, workspace)
        if block is None:
            continue
        servers.extend(
            (str(name), entry)
            for name, entry in block.items()
            if isinstance(entry, Mapping)
        )
    return tuple(servers), []


def _descend(
    raw: Any, key_path: Sequence[str], workspace: Path | None
) -> Mapping[str, Any] | None:
    node = raw
    for key in key_path:
        if not isinstance(node, Mapping):
            return None
        if key == WORKSPACE_TOKEN:
            node = _workspace_entry(node, workspace)
        else:
            node = node.get(key)
    return node if isinstance(node, Mapping) else None


def _workspace_entry(node: Mapping[str, Any], workspace: Path | None) -> Any:
    """The entry keyed by the workspace path, however that path is spelled."""
    if workspace is None:
        return None
    target = _resolved(workspace)
    for key, value in node.items():
        if (
            isinstance(key, str)
            and key.startswith("/")
            and _resolved(Path(key)) == target
        ):
            return value
    return None


# --- planning ----------------------------------------------------------------


def plan_import(
    loaded: LoadedFile,
    *,
    existing: Mapping[str, ConnectorConfig],
    rename_conflicts: bool,
) -> ImportPlan:
    """Decide, per server, the entry to save or why it is skipped. Writes nothing."""
    known: dict[str, ConnectorConfig] = dict(existing)
    rows: list[ImportRow] = []
    entries: list[_Planned] = []
    for source_name, entry in loaded.servers:
        kind: ImportKind = (
            "local"
            if "command" in entry
            or "cmd" in entry
            or entry.get("type") in ("builtin", "platform")
            else "remote"
        )
        slug = _slug(source_name)
        try:
            spec = to_spec(loaded.dialect, entry)
        except SkipServer as skip:
            rows.append(
                ImportRow(source_name, slug, kind, "skipped", f"skipped: {skip.reason}")
            )
            continue
        except ConnectorError as exc:
            rows.append(ImportRow(source_name, slug, kind, "failed", exc.message))
            continue
        twin = known.get(slug)
        if twin is not None and _same_server(twin, spec):
            rows.append(
                ImportRow(
                    source_name, slug, spec.kind, "skipped", f"skipped: same as {slug}"
                )
            )
            continue
        name = _free(slug, set(known)) if rename_conflicts else slug
        if name in known:
            rows.append(ImportRow(source_name, name, spec.kind, "skipped", NAME_TAKEN))
            continue
        try:
            planned = _plan_server(name, spec, project=loaded.scope == "project")
        except ConnectorError as exc:
            rows.append(ImportRow(source_name, name, spec.kind, "failed", exc.message))
            continue
        known[name] = planned.config
        entries.append(planned)
        failed = planned.config.transport == "sse"
        detail = (SSE_FAILED if failed else "imported") + (
            f" (renamed from {source_name})" if name != source_name else ""
        )
        rows.append(
            ImportRow(
                source_name, name, spec.kind, "failed" if failed else "imported", detail
            )
        )
    return ImportPlan(tuple(rows), tuple(entries))


def apply_import(
    plan: ImportPlan, *, home: Path, credentials: CredentialStore
) -> ImportResult:
    """Save secrets, then the entries, in that order.

    The caller holds `connectors_locked`. A yaml write that fails after the
    secrets were saved leaves only unused credential files behind.
    """
    entries = dict(load_connectors(home).entries)
    for planned in plan.entries:
        if planned.secret is not None:
            credentials.set(planned.config.name, planned.secret)
    for planned in plan.entries:
        entries[planned.config.name] = planned.config
    if plan.entries:
        save_connectors(home, entries)
    return ImportResult(
        rows=plan.rows,
        imported=len(plan.entries),
        values=sum(p.values for p in plan.entries),
    )


def _same_server(config: ConnectorConfig, spec: ServerSpec) -> bool:
    if spec.kind == "local":
        return config.command == spec.command and list(config.args) == spec.args
    return config.url is not None and config.url == spec.url


def _plan_server(name: str, spec: ServerSpec, *, project: bool) -> _Planned:
    if not is_valid_name(name):
        raise ConnectorConfigError(f"failed: {name} isn't a usable name")
    return (
        _plan_local(name, spec)
        if spec.kind == "local"
        else _plan_remote(name, spec, project)
    )


def _plan_local(name: str, spec: ServerSpec) -> _Planned:
    notes = [REVIEW_NOTE, *spec.notes]
    env_allow, values, env_notes = _env(spec.env)
    notes.extend(env_notes)
    for extra in spec.env_names:
        if _ENV_NAME_RE.fullmatch(extra) and extra not in env_allow:
            env_allow.append(extra)
    cwd = spec.cwd
    if cwd is not None and not (isinstance(cwd, str) and Path(cwd).is_absolute()):
        notes.append("set cwd by hand")
        cwd = None
    config = _validated(
        name=name,
        command=spec.command,
        args=spec.args,
        env_allow=env_allow,
        cwd=cwd or "workspace",
        enabled=False,
        note="; ".join(notes),
    )
    secret = (
        {"v": CREDENTIAL_VERSION, "kind": ENV_KIND, "env": values} if values else None
    )
    return _Planned(config, secret, len(values))


def _plan_remote(name: str, spec: ServerSpec, project: bool) -> _Planned:
    assert spec.url is not None
    try:
        check_url(spec.url)
    except ConnectorConfigError as exc:
        raise ConnectorConfigError(f"failed: {exc.message}") from None
    token, token_env, notes = _headers(spec.headers)
    if spec.token_env is not None:
        token, token_env = None, spec.token_env
    notes = [*spec.notes, *notes]
    if project:
        notes.insert(0, REVIEW_NOTE)
    config = _validated(
        name=name,
        url=spec.url,
        transport="sse" if spec.sse else None,
        auth="bearer" if token or token_env else "none",
        token_env=token_env,
        enabled=spec.enabled and not project,
        note="; ".join(notes) or None,
    )
    secret = bearer_credential(token) if token else None
    return _Planned(config, secret, 1 if token else 0)


def parse_reference(text: str) -> Reference | None:
    """`text` when it is nothing but a reference to a variable, else None."""
    match = _REFERENCE_RE.fullmatch(text)
    if match is None:
        return None
    bare, braced, default, curly = match.groups()
    return Reference(bare or braced or curly, default)


def _env(raw: Mapping[str, Any]) -> tuple[list[str], dict[str, str], list[str]]:
    """Allowlisted names, literal values for the store, and notes for the rest."""
    allow: list[str] = []
    values: dict[str, str] = {}
    notes: list[str] = []
    for key, value in raw.items():
        name = str(key)
        if not _ENV_NAME_RE.fullmatch(name) or not isinstance(
            value, str | int | float | bool
        ):
            notes.append(f"set {name} by hand")
            continue
        text = value if isinstance(value, str) else json.dumps(value)
        reference = parse_reference(text)
        if reference is not None and reference.name == name:
            allow.append(name)
            if reference.default is not None:
                notes.append(f"default for {name} not imported")
        elif reference is not None or text.startswith(("${input:", "!")):
            # Another variable, an editor prompt, or a command to run: Aldwin
            # can't know the value and must never run anything here.
            notes.append(f"set {name} by hand")
        else:
            allow.append(name)
            values[name] = text
    return allow, values, notes


def _headers(raw: Mapping[str, Any]) -> tuple[str | None, str | None, list[str]]:
    """The bearer token as a literal or as a variable name, and notes for the rest."""
    token: str | None = None
    token_env: str | None = None
    notes: list[str] = []
    for key, value in raw.items():
        bearer = (
            _BEARER_RE.fullmatch(value)
            if str(key).lower() == "authorization" and isinstance(value, str)
            else None
        )
        if bearer is None:
            notes.append(f"skipped header {key}")
            continue
        credential = bearer.group(1)
        reference = parse_reference(credential)
        if reference is not None:
            token_env = reference.name
        elif not any(ch.isspace() for ch in credential) and "${" not in credential:
            token = credential
        else:
            notes.append(f"skipped header {key}")
    return token, token_env, notes


# --- helpers -------------------------------------------------------------------


class _Unreadable(Exception):
    pass


def _unreadable(path: Path, label: str, reason: str) -> ConnectorConfigError:
    return ConnectorConfigError(
        f"{path} isn't a {label} file Aldwin can read: {reason}."
    )


def _parse_reason(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return exc.msg.lower()
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem:
        return exc.problem
    return str(exc).split("\n", 1)[0].lower() or "it can't be parsed"


def _mb(size: int) -> str:
    return f"{max(1, round(size / 1_048_576))} MB"


def _format_for(path: Path) -> Format:
    suffix = path.suffix.lower()
    if suffix == ".toml":
        return "toml"
    if suffix in (".yaml", ".yml"):
        return "yaml"
    return "json"


def _is_map(value: Any) -> bool:
    return isinstance(value, Mapping)


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def strip_jsonc(text: str) -> str:
    """Remove `//` and `/* */` comments and trailing commas, leaving strings alone."""
    return _scan(_scan(text, comments=True), comments=False)


def _scan(text: str, *, comments: bool) -> str:
    """One pass outside strings: drop comments, or drop commas before `}`/`]`."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == '"':
            end = _string_end(text, i)
            out.append(text[i:end])
            i = end
        elif comments and text.startswith("//", i):
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
        elif comments and text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = n if close == -1 else close + 2
        elif not comments and text[i] == "," and _closes_next(text, i + 1):
            i += 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _closes_next(text: str, start: int) -> bool:
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    return i < len(text) and text[i] in "}]"


def _string_end(text: str, start: int) -> int:
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == '"':
            return i + 1
        i += 1
    return len(text)


def _slug(source_name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", source_name.lower()).strip("-_")[:30]
    return slug.strip("-_") or "connector"


def _free(base: str, taken: set[str]) -> str:
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"-{n}"
        candidate = base[: 30 - len(suffix)] + suffix
        n += 1
    return candidate


def _validated(**fields: Any) -> ConnectorConfig:
    try:
        return ConnectorConfig(**fields)
    except ValueError as exc:
        raise ConnectorConfigError(f"failed: {_first_error(exc)}") from None


def _first_error(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        first = errors()[0]
        return str(first.get("ctx", {}).get("error", first.get("msg", exc)))
    return str(exc)
