"""What `propose_connector` would add, worked out from the model's arguments.

Resolving never saves, connects or asks. The policy wrapper shows the user a
preview of the proposal; the connector manager runs it only after a yes. The
result the model sees is one short line that never carries a URL, token or code.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from aldwin.core.approval_log import redact_secrets
from aldwin.core.connectors.catalog import CatalogEntry
from aldwin.core.connectors.config import (
    ConnectorConfig,
    check_url,
    suggest_name,
    suggest_name_for_command,
)
from aldwin.core.connectors.errors import ConnectorConfigError
from aldwin.core.connectors.local import parse_command_line

EXACTLY_ONE: Final[str] = "Give exactly one of name, url or command."
MAX_REASON_CHARS: Final[int] = 80
_LINK_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)

ProposalKind = Literal["catalog", "url", "command", "enable"]
OutcomeKind = Literal["connected", "cancelled", "sign_in_failed", "changed", "failed"]


@dataclass(frozen=True)
class ConnectorProposal:
    """A connector to add, or a saved one to turn on. Nothing is saved yet."""

    kind: ProposalKind
    config: ConnectorConfig
    catalog_entry: CatalogEntry | None = None

    @property
    def name(self) -> str:
        return self.config.name

    def fingerprint(self) -> str:
        """Equal only when the same connector would be added the same way."""
        body = {"kind": self.kind, "config": self.config.model_dump(mode="json")}
        return json.dumps(body, sort_keys=True)


@dataclass(frozen=True)
class ProposeOutcome:
    kind: OutcomeKind
    name: str
    tools: int = 0
    loaded: int = 0
    reason: str = ""


def resolve_proposal(
    *,
    name: str | None,
    url: str | None,
    command: str | None,
    args: Sequence[str] | None,
    catalog: Sequence[CatalogEntry],
    configs: Mapping[str, ConnectorConfig],
) -> ConnectorProposal:
    """Turn the model's arguments into one proposal.

    Raises `ConnectorConfigError` with a one-line reason the model can act on:
    more or fewer than one target, a bad URL or command, an unknown name, or a
    connector that is already on (`already connected: <name>`).
    """
    given = [value for value in (name, url, command) if value]
    if len(given) != 1:
        raise ConnectorConfigError(EXACTLY_ONE)
    if args and not command:
        raise ConnectorConfigError("args only go with command.")
    if args is not None and (
        isinstance(args, str) or not all(isinstance(a, str) for a in args)
    ):
        raise ConnectorConfigError("args must be a list of strings.")
    if name:
        return _by_name(name.strip(), catalog, configs)
    if url:
        return _by_url(url.strip(), catalog, configs)
    assert command is not None
    return _by_command(command, args, configs)


def format_outcome(outcome: ProposeOutcome) -> str:
    """The one line the model sees after the user said yes."""
    name = outcome.name
    if outcome.kind == "connected":
        if outcome.loaded >= outcome.tools:
            return f"connected: {name}, {outcome.tools} tools loaded"
        return (
            f"connected: {name}, {outcome.tools} tools; {outcome.loaded} loaded, "
            "call find_connector_tools for the rest"
        )
    if outcome.kind == "cancelled":
        return "cancelled by user"
    if outcome.kind == "changed":
        return "cancelled: connector changed while waiting"
    prefix = "sign-in failed" if outcome.kind == "sign_in_failed" else "failed"
    return f"{prefix}: {short_reason(outcome.reason)}"


def short_reason(text: str) -> str:
    """A reason safe to show the model: no links, no secrets, one short line."""
    cleaned = redact_secrets(_LINK_RE.sub("[link]", text))
    cleaned = " ".join(cleaned.split()).rstrip(".")
    if len(cleaned) > MAX_REASON_CHARS:
        cleaned = cleaned[: MAX_REASON_CHARS - 1].rstrip() + "…"
    return cleaned or "unknown error"


def _by_name(
    name: str, catalog: Sequence[CatalogEntry], configs: Mapping[str, ConnectorConfig]
) -> ConnectorProposal:
    key = name.casefold()
    saved = configs.get(key)
    if saved is not None:
        return _existing(saved)
    entry = _catalog_match(key, catalog)
    if entry is None:
        shown = name[:40]
        raise ConnectorConfigError(
            f"No connector called {shown}. Ask the user for the server's MCP URL, "
            "or point them to /connectors."
        )
    if entry.name in configs:
        return _existing(configs[entry.name])
    return _from_catalog(entry, entry.name)


def _by_url(
    url: str, catalog: Sequence[CatalogEntry], configs: Mapping[str, ConnectorConfig]
) -> ConnectorProposal:
    check_url(url)
    for saved in configs.values():
        if saved.url == url:
            return _existing(saved)
    entry = next((e for e in catalog if e.url == url), None)
    if entry is not None:
        name = entry.name if entry.name not in configs else suggest_name(url, configs)
        return _from_catalog(entry, name)
    # A server nobody vetted: its tools ask until the user trusts it.
    config = ConnectorConfig(
        name=suggest_name(url, configs), url=url, trust_read_only_hints=False
    )
    return ConnectorProposal("url", config)


def _by_command(
    command: str, args: Sequence[str] | None, configs: Mapping[str, ConnectorConfig]
) -> ConnectorProposal:
    if args is not None:
        program, rest = command.strip(), list(args)
        if not program:
            raise ConnectorConfigError("Enter the command that starts the server.")
    else:
        program, rest = parse_command_line(command)
    for saved in configs.values():
        if saved.command == program and list(saved.args) == rest:
            return _existing(saved)
    name = suggest_name_for_command([program, *rest], configs)
    return ConnectorProposal(
        "command", ConnectorConfig(name=name, command=program, args=rest)
    )


def _existing(config: ConnectorConfig) -> ConnectorProposal:
    if config.enabled:
        raise ConnectorConfigError(f"already connected: {config.name}")
    return ConnectorProposal("enable", config)


def _catalog_match(key: str, catalog: Sequence[CatalogEntry]) -> CatalogEntry | None:
    for match in (
        lambda e: e.name == key,
        lambda e: e.title.casefold() == key,
        lambda e: key in (k.casefold() for k in e.keywords),
    ):
        entry = next((e for e in catalog if match(e)), None)
        if entry is not None:
            return entry
    return None


def _from_catalog(entry: CatalogEntry, name: str) -> ConnectorProposal:
    config = ConnectorConfig(
        name=name,
        url=entry.url,
        title=entry.title,
        auth=entry.auth,
        oauth=entry.oauth,
    )
    return ConnectorProposal("catalog", config, entry)
