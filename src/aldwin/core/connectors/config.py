"""`~/.aldwin/connectors.yaml`: connector entries, checks and change detection."""

from __future__ import annotations

import fcntl
import logging
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    field_validator,
)

from aldwin.core.connectors.errors import ConnectorConfigError
from aldwin.core.connectors.oauth_messages import DEFAULT_CLIENT_METADATA_URL
from aldwin.core.home import FILE_MODE, atomic_write

logger = logging.getLogger("aldwin.core.connectors.config")

CONNECTORS_FILENAME: Final[str] = "connectors.yaml"
CREDENTIALS_SUBDIR: Final[str] = "credentials/mcp"
FILE_VERSION: Final[int] = 1
NAME_PATTERN: Final[str] = r"^[a-z0-9][a-z0-9_-]{0,29}$"
_NAME_RE = re.compile(NAME_PATTERN)
_LOCAL_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1"})
_GENERIC_LABELS: Final[frozenset[str]] = frozenset({"mcp", "api", "www", "server"})

AuthKind = Literal["none", "bearer", "oauth"]
ToolSetting = Literal["auto", "ask", "off"]

_EXPECTED_BY_ERROR: Final[dict[str, str]] = {
    "bool_type": "a boolean",
    "string_type": "a string",
    "dict_type": "a mapping",
    "literal_error": "one of the allowed values",
    "string_pattern_mismatch": "a valid name",
    "missing": "set",
}

# Errors already logged in this process, so a bad file warns once, not every turn.
_logged_errors: set[str] = set()


class OAuthSettings(BaseModel):
    """Browser sign-in settings for one connector."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    client_id: StrictStr | None = None  # a pre-registered client ID


class ConnectorConfig(BaseModel):
    """One connector entry. The YAML key is `name`; the file never holds a secret."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: StrictStr = Field(pattern=NAME_PATTERN)
    url: StrictStr
    title: StrictStr | None = None
    enabled: StrictBool = True
    auth: AuthKind = "none"
    token_env: StrictStr | None = None
    trust_read_only_hints: StrictBool = True
    tools: dict[str, ToolSetting] = Field(default_factory=dict)
    oauth: OAuthSettings | None = None

    @field_validator("tools", mode="before")
    @classmethod
    def _bare_off(cls, value: Any) -> Any:
        # YAML 1.1 reads an unquoted `off` as false.
        if isinstance(value, dict):
            return {k: ("off" if v is False else v) for k, v in value.items()}
        return value

    @field_validator("url")
    @classmethod
    def _url_allowed(cls, value: str) -> str:
        try:
            check_url(value)
        except ConnectorConfigError as exc:
            raise ValueError(exc.message) from None
        return value

    @property
    def display_title(self) -> str:
        """The `title`, or the name with its first letter upper-cased."""
        return self.title or self.name[:1].upper() + self.name[1:]


@dataclass(frozen=True)
class ConnectorsLoad:
    """Result of reading `connectors.yaml`. `error` is set when the file is invalid."""

    entries: dict[str, ConnectorConfig] = field(default_factory=dict)
    error: str | None = None
    # The CIMD client ID; self-hosters point it at their own copy.
    oauth_client_metadata_url: str = DEFAULT_CLIENT_METADATA_URL


def connectors_path(home: Path) -> Path:
    return home / CONNECTORS_FILENAME


def credentials_dir(home: Path) -> Path:
    return home / CREDENTIALS_SUBDIR


def load_connectors(home: Path) -> ConnectorsLoad:
    """Read `connectors.yaml`. Never raises: a bad file gives an error, no entries."""
    path = connectors_path(home)
    if not path.exists():
        return ConnectorsLoad()
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return _invalid("connectors.yaml: the file must be valid YAML")
    if parsed is None:
        return ConnectorsLoad()
    if not isinstance(parsed, dict):
        return _invalid("connectors.yaml: the file must be a mapping")

    metadata_url = parsed.get("oauth_client_metadata_url", DEFAULT_CLIENT_METADATA_URL)
    if not _is_https_url(metadata_url):
        return _invalid(
            "connectors.yaml: oauth_client_metadata_url must be an https URL"
        )

    raw = parsed.get("connectors") or {}
    if not isinstance(raw, dict):
        return _invalid("connectors.yaml: connectors must be a mapping")

    entries: dict[str, ConnectorConfig] = {}
    for name, body in raw.items():
        if not isinstance(body, dict):
            return _invalid(f"connectors.yaml: connectors.{name} must be a mapping")
        try:
            entries[str(name)] = ConnectorConfig.model_validate({**body, "name": name})
        except ValidationError as exc:
            return _invalid(_describe(str(name), exc))
    return ConnectorsLoad(entries=entries, oauth_client_metadata_url=metadata_url)


def save_connectors(home: Path, entries: Mapping[str, ConnectorConfig]) -> None:
    """Write all entries atomically with mode 0600, keyed by name.

    A custom `oauth_client_metadata_url` already in the file is kept.
    """
    header: dict[str, Any] = {"version": FILE_VERSION}
    metadata_url = load_connectors(home).oauth_client_metadata_url
    if metadata_url != DEFAULT_CLIENT_METADATA_URL:
        header["oauth_client_metadata_url"] = metadata_url
    body: dict[str, Any] = {}
    for name, config in sorted(entries.items()):
        data = config.model_dump(exclude={"name"}, exclude_none=True)
        if not data["tools"]:
            del data["tools"]
        body[name] = data
    text = yaml.safe_dump(
        {**header, "connectors": body},
        sort_keys=False,
        allow_unicode=True,
    )
    atomic_write(connectors_path(home), text)


def _is_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.hostname)


def is_valid_name(name: str) -> bool:
    """True when `name` matches `^[a-z0-9][a-z0-9_-]{0,29}$`."""
    return _NAME_RE.fullmatch(name) is not None


@contextmanager
def connectors_locked(home: Path) -> Iterator[None]:
    """Hold an exclusive lock for a read-modify-write of `connectors.yaml`.

    Another process adding the same name waits, then sees the first one's entry.
    """
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        home / f"{CONNECTORS_FILENAME}.lock", os.O_RDWR | os.O_CREAT, FILE_MODE
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def check_name(name: str, existing: Iterable[str]) -> None:
    """Raise `ConnectorConfigError` unless `name` is valid and unused."""
    if not is_valid_name(name):
        raise ConnectorConfigError(
            "Connector names use lowercase letters, digits, - and _, "
            f"up to 30 characters (got {name})"
        )
    if name in set(existing):
        raise ConnectorConfigError(f"A connector named {name} already exists")


def check_url(url: str) -> None:
    """Allow `https://`, or `http://` only for localhost and 127.0.0.1."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme == "https" and host:
        return
    if parts.scheme == "http" and host in _LOCAL_HOSTS:
        return
    raise ConnectorConfigError(f"Connector URL must use https:// (got {url})")


def suggest_name(url: str, existing: Iterable[str]) -> str:
    """Suggest a free name from the URL's host: `mcp.context7.com` -> `context7`."""
    labels = (urlsplit(url).hostname or "").split(".")
    if len(labels) > 1:
        labels = labels[:-1]
    while len(labels) > 1 and labels[0] in _GENERIC_LABELS:
        labels = labels[1:]
    base = re.sub(r"[^a-z0-9_-]", "-", labels[0].lower()).lstrip("-_")[:30]
    base = base or "connector"

    taken = set(existing)
    candidate, n = base, 2
    while candidate in taken:
        suffix = f"-{n}"
        candidate = f"{base[: 30 - len(suffix)]}{suffix}"
        n += 1
    return candidate


def connectors_fingerprint(home: Path) -> tuple[int, ...]:
    """A cheap "something may have changed" gate: mtime and size of the config and
    credential files. Lock files are ignored. It never restarts anything itself.
    """
    paths = [connectors_path(home)]
    creds = credentials_dir(home)
    if creds.is_dir():
        paths.extend(sorted(creds.glob("*.json")))
    out: list[int] = []
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            out.extend((0, 0))
            continue
        out.extend((stat.st_mtime_ns, stat.st_size))
    return tuple(out)


def _describe(name: str, exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    if first["type"] == "value_error" and loc == "url":
        return f"connectors.yaml: connectors.{name}.url must use https://"
    expected = _EXPECTED_BY_ERROR.get(first["type"], "a valid value")
    target = f"connectors.{name}" + (f".{loc}" if loc and loc != "name" else "")
    if loc == "name":
        expected = "a valid name (lowercase letters, digits, - and _)"
    return f"connectors.yaml: {target} must be {expected}"


def _invalid(message: str) -> ConnectorsLoad:
    if message not in _logged_errors:
        _logged_errors.add(message)
        logger.warning("%s; connectors are off until it is fixed", message)
    return ConnectorsLoad(error=message)
