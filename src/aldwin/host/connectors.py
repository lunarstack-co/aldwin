"""Connector lifecycle in the host: start in the background, reconnect with backoff,
follow tool-list changes, and re-add tools after every session rebuild.

`ConnectorAdmin` changes config and credentials. `ConnectorManager` keeps one
live connection per enabled connector and keeps the session's tools in step.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agentling import AgentSession, Tool

from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.connectors.config import (
    ConnectorConfig,
    ConnectorsLoad,
    ToolSetting,
    check_name,
    check_url,
    connectors_fingerprint,
    connectors_locked,
    load_connectors,
    save_connectors,
)
from aldwin.core.connectors.connection import (
    BearerAuth,
    McpConnection,
    build_auth,
    probe_connector,
)
from aldwin.core.connectors.credentials import (
    CredentialStore,
    FileCredentialStore,
    bearer_credential,
    credential_identity,
)
from aldwin.core.connectors.errors import ConnectorConfigError, ConnectorError
from aldwin.core.connectors.mcp_tool import McpTool, RemoteToolSpec
from aldwin.core.connectors.policy import connector_policy
from aldwin.core.connectors.state import (
    ConnectorSnapshot,
    ConnectorStatus,
    format_connectors_block,
    tool_infos,
)
from aldwin.core.policy import (
    PolicyAskFn,
    PolicySession,
    register_runtime_policy,
    unregister_runtime_policy,
    wrap_tools_with_policy,
)

logger = logging.getLogger("aldwin.host.connectors")

BACKOFF_START_S = 30.0
BACKOFF_MAX_S = 600.0

ConnectionFactory = Callable[..., McpConnection]


@dataclass(frozen=True)
class ConnectorEvent:
    """Something a door should hear about: a status change or a one-line notice."""

    kind: Literal["status", "notice"]
    snapshot: ConnectorSnapshot | None = None
    message: str = ""


@dataclass(frozen=True)
class AddResult:
    """What adding (or probing) a connector found."""

    config: ConnectorConfig
    snapshot: ConnectorSnapshot

    @property
    def tool_count(self) -> int:
        return len(self.snapshot.tools)

    @property
    def read(self) -> int:
        return sum(1 for t in self.snapshot.tools if t.effective == "auto")

    @property
    def write(self) -> int:
        return sum(1 for t in self.snapshot.tools if t.effective == "ask")


class ConnectorAdmin:
    """Config, credentials and checks. Needs no live session, so the shell
    commands use it directly.
    """

    def __init__(
        self,
        home: Path,
        store: CredentialStore | None = None,
        *,
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self.home = home
        self.store = store or FileCredentialStore(home)
        self._environ = environ

    def load(self) -> ConnectorsLoad:
        return load_connectors(self.home)

    def auth_for(self, config: ConnectorConfig, token: str | None = None) -> Any:
        """The auth to send: an explicit token, else the stored or env token."""
        if token:
            return BearerAuth(token)
        return build_auth(config, self.store, self._environ)

    def check_new(self, config: ConnectorConfig) -> None:
        """Raise `ConnectorConfigError` for a bad URL or a taken name."""
        check_url(config.url)
        check_name(config.name, self.load().entries)

    async def probe(
        self, config: ConnectorConfig, token: str | None = None
    ) -> list[RemoteToolSpec]:
        return await probe_connector(config, self.auth_for(config, token))

    def save_new(self, config: ConnectorConfig, token: str | None) -> None:
        """Save a new entry (and its token) unless another process took the name."""
        with connectors_locked(self.home):
            entries = dict(self._entries_or_raise())
            check_name(config.name, entries)
            if token:
                self.store.set(config.name, bearer_credential(token))
            entries[config.name] = config
            save_connectors(self.home, entries)

    def update(self, name: str, **changes: Any) -> ConnectorConfig:
        """Change fields of one entry and save; returns the new entry."""
        return self._rewrite(name, lambda _current: changes)

    def set_tool_setting(
        self, name: str, tool: str, setting: ToolSetting | Literal["default"]
    ) -> ConnectorConfig:
        def changes(current: ConnectorConfig) -> dict[str, Any]:
            tools = dict(current.tools)
            if setting == "default":
                tools.pop(tool, None)
            else:
                tools[tool] = setting
            return {"tools": tools}

        return self._rewrite(name, changes)

    def _rewrite(
        self, name: str, changes: Callable[[ConnectorConfig], dict[str, Any]]
    ) -> ConnectorConfig:
        """Read, change and save one entry under a single lock, so a change
        another process makes in between is never lost.
        """
        with connectors_locked(self.home):
            entries = dict(self._entries_or_raise())
            current = self._existing(entries, name)
            updated = ConnectorConfig.model_validate(
                {**current.model_dump(), **changes(current)}
            )
            entries[name] = updated
            save_connectors(self.home, entries)
            return updated

    def remove(self, name: str) -> None:
        """Delete the entry and its saved token."""
        with connectors_locked(self.home):
            entries = dict(self._entries_or_raise())
            self._existing(entries, name)
            del entries[name]
            save_connectors(self.home, entries)
            self.store.delete(name)

    def _entries_or_raise(self) -> dict[str, ConnectorConfig]:
        load = self.load()
        if load.error is not None:
            raise ConnectorConfigError(f"Fix {load.error} first.")
        return load.entries

    @staticmethod
    def _existing(entries: Mapping[str, ConnectorConfig], name: str) -> ConnectorConfig:
        if name not in entries:
            raise ConnectorConfigError(f"No connector named {name}.")
        return entries[name]


@dataclass
class _Live:
    """One connector the manager is running."""

    config: ConnectorConfig
    identity: tuple[Any, ...] | None = None
    connection: McpConnection | None = None
    status: ConnectorStatus = "connecting"
    error: str | None = None
    retry_in_s: int | None = None
    specs: list[RemoteToolSpec] = field(default_factory=list)
    tools: dict[str, Tool] = field(default_factory=dict)
    was_connected: bool = False
    lost: asyncio.Event = field(default_factory=asyncio.Event)
    supervisor: asyncio.Task[None] | None = None


def default_connection_factory(config: ConnectorConfig, **kwargs: Any) -> McpConnection:
    return McpConnection(config, **kwargs)


class ConnectorManager:
    """Runs every enabled connector and keeps the live session's tools current."""

    def __init__(
        self,
        *,
        admin: ConnectorAdmin,
        policy_session: PolicySession,
        ask: PolicyAskFn,
        approval_log: ApprovalLogger | None,
        emit: Callable[[ConnectorEvent], Awaitable[None]],
        connection_factory: ConnectionFactory = default_connection_factory,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.admin = admin
        self._policy_session = policy_session
        self._ask = ask
        self._approval_log = approval_log
        self._emit = emit
        self._connection_factory = connection_factory
        self._sleep = sleep
        self._session: AgentSession | None = None
        self._configs: dict[str, ConnectorConfig] = {}
        self._live: dict[str, _Live] = {}
        self._fingerprint: tuple[int, ...] | None = None
        self.load_error: str | None = None

    # --- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Start every enabled connector in the background and return at once."""
        load = self.admin.load()
        self.load_error = load.error
        self._configs = dict(load.entries)
        self._fingerprint = connectors_fingerprint(self.admin.home)
        for config in self._configs.values():
            if config.enabled:
                self._start_live(config)

    def attach(self, session: AgentSession) -> None:
        """Use `session` from now on and add every connector tool to it."""
        self._session = session
        tools = [t for live in self._live.values() for t in live.tools.values()]
        self._join_peers(tools)
        if tools:
            session.add_tools(tools, replace=True)

    async def reconcile(self) -> None:
        """Pick up changes another process made; cheap when nothing did."""
        fingerprint = connectors_fingerprint(self.admin.home)
        if fingerprint == self._fingerprint:
            return
        self._fingerprint = fingerprint
        load = self.admin.load()
        self.load_error = load.error
        if load.error is not None:
            return  # keep what runs until the file is valid again
        old, new = self._configs, dict(load.entries)
        self._configs = new
        for name in old.keys() - new.keys():
            await self._stop_live(name)
        for name, config in new.items():
            live = self._live.get(name)
            if not config.enabled:
                await self._stop_live(name)
                continue
            if live is None:
                self._start_live(config)
                continue
            identity = credential_identity(self.admin.store.get(name))
            if config != live.config or identity != live.identity:
                await self._stop_live(name)
                self._start_live(config)

    async def aclose(self, *, timeout: float = 5.0) -> None:
        """Stop every connector; a session that hangs past `timeout` is abandoned."""
        names = list(self._live)
        await asyncio.gather(
            *(self._stop_live(n, close_timeout=timeout) for n in names),
            return_exceptions=True,
        )

    # --- requests -----------------------------------------------------------

    async def add(
        self, config: ConnectorConfig, *, token: str | None, dry_run: bool
    ) -> AddResult:
        """Probe (`dry_run`) or connect and save. Nothing is saved on failure."""
        self.admin.check_new(config)
        if dry_run:
            specs = await self._probe(config, token)
            return AddResult(config, self._snapshot_of(config, "connected", specs))

        live = _Live(config=config)
        await self._emit(ConnectorEvent("status", self._snapshot(live)))
        connection = self._new_connection(live, self.admin.auth_for(config, token))
        specs = await connection.open()
        try:
            self.admin.save_new(config, token)
        except ConnectorError:
            await connection.close()
            raise
        self._configs[config.name] = config
        self._fingerprint = connectors_fingerprint(self.admin.home)
        live.identity = credential_identity(self.admin.store.get(config.name))
        self._live[config.name] = live
        await self._on_connected(live, connection, specs)
        live.supervisor = asyncio.create_task(self._supervise(live))
        return AddResult(config, self._snapshot(live))

    async def enable(self, name: str) -> ConnectorSnapshot:
        config = self.admin.update(name, enabled=True)
        self._configs[name] = config
        self._fingerprint = connectors_fingerprint(self.admin.home)
        await self._stop_live(name)
        live = await self._connect_now(config)
        return self._snapshot(live)

    async def disable(self, name: str) -> ConnectorSnapshot:
        config = self.admin.update(name, enabled=False)
        self._configs[name] = config
        self._fingerprint = connectors_fingerprint(self.admin.home)
        await self._stop_live(name)
        snapshot = self._snapshot_of(config, "disabled", [])
        await self._emit(ConnectorEvent("status", snapshot))
        return snapshot

    async def reconnect(self, name: str) -> ConnectorSnapshot:
        config = self._require(name)
        await self._stop_live(name)
        live = await self._connect_now(config)
        return self._snapshot(live)

    async def remove(self, name: str) -> None:
        self._require(name)
        await self._stop_live(name)
        self.admin.remove(name)
        self._configs.pop(name, None)
        self._fingerprint = connectors_fingerprint(self.admin.home)

    async def set_tool_setting(
        self, name: str, tool: str, setting: ToolSetting | Literal["default"]
    ) -> None:
        self._require(name)
        self._reconfigured(self.admin.set_tool_setting(name, tool, setting))

    async def set_trust_hints(self, name: str, trust: bool) -> None:
        self._require(name)
        self._reconfigured(self.admin.update(name, trust_read_only_hints=trust))

    # --- views --------------------------------------------------------------

    def snapshots(self) -> list[ConnectorSnapshot]:
        out: list[ConnectorSnapshot] = []
        for name, config in sorted(self._configs.items()):
            live = self._live.get(name)
            if live is not None:
                out.append(self._snapshot(live))
            else:
                out.append(self._snapshot_of(config, "disabled", []))
        return out

    def context_block(self) -> str:
        return format_connectors_block(self.snapshots())

    def labels(self, model_name: str) -> tuple[str | None, str | None]:
        """`(connector title, server tool name)` for a connector tool, else Nones."""
        for live in self._live.values():
            tool = live.tools.get(model_name)
            if tool is not None:
                inner = getattr(tool, "inner_tool", tool)
                if isinstance(inner, McpTool):
                    return inner.connector_title, inner.remote_name
        return None, None

    # --- internals ----------------------------------------------------------

    def _require(self, name: str) -> ConnectorConfig:
        if name not in self._configs:
            raise ConnectorConfigError(f"No connector named {name}.")
        return self._configs[name]

    def _start_live(self, config: ConnectorConfig) -> _Live:
        live = _Live(
            config=config,
            identity=credential_identity(self.admin.store.get(config.name)),
        )
        self._live[config.name] = live
        self._spawn_supervisor(live)
        return live

    async def _connect_now(self, config: ConnectorConfig) -> _Live:
        """Try to connect now; if that fails, keep retrying in the background."""
        live = _Live(
            config=config,
            identity=credential_identity(self.admin.store.get(config.name)),
        )
        self._live[config.name] = live
        connected = await self._attempt(live)
        if self._tracked(live):
            self._spawn_supervisor(live, wait_first=not connected)
        return live

    def _tracked(self, live: _Live) -> bool:
        """False once `live` was stopped (disabled, removed, restarted)."""
        return self._live.get(live.config.name) is live

    def _spawn_supervisor(self, live: _Live, *, wait_first: bool = False) -> None:
        task = asyncio.create_task(self._supervise(live, wait_first=wait_first))
        task.add_done_callback(lambda done: self._supervisor_ended(live, done))
        live.supervisor = task

    def _supervisor_ended(self, live: _Live, task: asyncio.Task[None]) -> None:
        """A supervisor only ends by cancellation or by being stopped; anything
        else is a bug, so log it and show the connector as failed.
        """
        if task.cancelled() or task.exception() is None:
            return
        logger.error(
            "connector %s stopped retrying",
            live.config.name,
            exc_info=task.exception(),
        )
        live.status, live.error = "failed", "stopped unexpectedly; see aldwin.log"

    async def _supervise(self, live: _Live, *, wait_first: bool = False) -> None:
        """Keep `live` connected: retry after 30 s, doubling up to 10 min."""
        delay = BACKOFF_START_S
        if wait_first:
            await self._sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX_S)
        while self._tracked(live):
            if live.connection is None:
                if not await self._attempt(live, retry_in=delay):
                    await self._sleep(delay)
                    delay = min(delay * 2, BACKOFF_MAX_S)
                    continue
                delay = BACKOFF_START_S
            await live.lost.wait()
            live.lost.clear()
            await self._drop(live, retry_in=delay)
            await self._emit(
                ConnectorEvent(
                    "notice",
                    message=f"{live.config.display_title} disconnected; retrying.",
                )
            )
            await self._sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX_S)

    async def _attempt(self, live: _Live, *, retry_in: float = BACKOFF_START_S) -> bool:
        """Try to connect once; report the result either way."""
        live.status, live.error, live.retry_in_s = "connecting", None, None
        await self._emit(ConnectorEvent("status", self._snapshot(live)))
        connection = self._new_connection(live, self.admin.auth_for(live.config))
        try:
            specs = await connection.open()
            if not self._tracked(live):
                # Stopped while connecting: don't bring its tools back.
                await connection.close()
                return False
        except ConnectorError as exc:
            if not self._tracked(live):
                return False
            live.status, live.error, live.retry_in_s = (
                "failed",
                exc.message,
                int(retry_in),
            )
            await self._emit(ConnectorEvent("status", self._snapshot(live)))
            return False
        await self._on_connected(live, connection, specs)
        return True

    async def _on_connected(
        self, live: _Live, connection: McpConnection, specs: list[RemoteToolSpec]
    ) -> None:
        reconnected = live.was_connected
        live.connection, live.was_connected = connection, True
        live.status, live.error, live.retry_in_s = "connected", None, None
        self._apply(live, specs)
        await self._emit(ConnectorEvent("status", self._snapshot(live)))
        if reconnected:
            await self._emit(
                ConnectorEvent(
                    "notice", message=f"{live.config.display_title} reconnected."
                )
            )

    async def _probe(
        self, config: ConnectorConfig, token: str | None
    ) -> list[RemoteToolSpec]:
        """Connect once to list the tools, then disconnect."""

        async def _ignore(*_args: Any) -> None:
            return None

        connection = self._connection_factory(
            config,
            auth=self.admin.auth_for(config, token),
            on_tools_changed=_ignore,
            on_lost=_ignore,
        )
        try:
            return await connection.open()
        finally:
            await connection.close()

    def _new_connection(self, live: _Live, auth: Any) -> McpConnection:
        holder: list[McpConnection] = []

        async def on_lost(error: ConnectorError) -> None:
            if holder and live.connection is holder[0]:
                live.error = error.message
                live.lost.set()

        async def on_tools_changed() -> None:
            if not holder or live.connection is not holder[0]:
                return
            try:
                specs = await holder[0].list_tools()
            except ConnectorError:
                return  # the lost path handles it
            # The connector may have stopped during the listing.
            if (
                live.connection is holder[0]
                and self._live.get(live.config.name) is live
            ):
                self._apply(live, specs)

        connection = self._connection_factory(
            live.config,
            auth=auth,
            on_tools_changed=on_tools_changed,
            on_lost=on_lost,
        )
        holder.append(connection)
        return connection

    def _apply(self, live: _Live, specs: list[RemoteToolSpec]) -> None:
        """Make the session's tools match `specs` for this connector."""
        live.specs = list(specs)
        assert live.connection is not None
        fresh: dict[str, Tool] = {}
        for spec in specs:
            policy = connector_policy(live.config, spec)
            if policy is None:
                continue  # "off": the model never sees it
            if policy.name in fresh:
                logger.warning(
                    "connector %s: tools collide on %s; skipping %s",
                    live.config.name,
                    policy.name,
                    spec.name,
                )
                continue
            register_runtime_policy(policy)
            fresh[policy.name] = McpTool(
                connector=live.config,
                spec=spec,
                connection=live.connection,
                tmp_dir=self.admin.home / "tmp",
            )
        wrapped = wrap_tools_with_policy(
            fresh.values(),
            session=self._policy_session,
            ask=self._ask,
            approval_log=self._approval_log,
            join=True,
        )
        gone = set(live.tools) - set(fresh)
        live.tools = {t.name: t for t in wrapped}
        if self._session is not None:
            self._session.add_tools(wrapped, replace=True)
            present = [n for n in gone if n in self._session.tools]
            self._session.remove_tools(present)
        self._forget(gone)

    def _reconfigured(self, config: ConnectorConfig) -> None:
        """Apply new settings to a running connector without reconnecting."""
        self._configs[config.name] = config
        self._fingerprint = connectors_fingerprint(self.admin.home)
        live = self._live.get(config.name)
        if live is None:
            return
        live.config = config
        if live.connection is not None:
            self._apply(live, live.specs)

    async def _drop(self, live: _Live, *, retry_in: float) -> None:
        """Remove this connector's tools and close its connection."""
        self._remove_tools(live)
        connection, live.connection = live.connection, None
        live.status = "failed"
        live.retry_in_s = int(retry_in)
        await self._emit(ConnectorEvent("status", self._snapshot(live)))
        if connection is not None:
            await connection.close()

    async def _stop_live(self, name: str, *, close_timeout: float = 5.0) -> None:
        live = self._live.pop(name, None)
        if live is None:
            return
        if live.supervisor is not None:
            live.supervisor.cancel()
            await asyncio.gather(live.supervisor, return_exceptions=True)
        self._remove_tools(live)
        if live.connection is not None:
            await live.connection.close(timeout=close_timeout)
            live.connection = None

    def _remove_tools(self, live: _Live) -> None:
        names = set(live.tools)
        live.tools = {}
        if self._session is not None:
            present = [n for n in names if n in self._session.tools]
            self._session.remove_tools(present)
        self._forget(names)

    def _forget(self, names: set[str]) -> None:
        for name in names:
            unregister_runtime_policy(name)
            self._policy_session.peer_tools.pop(name, None)

    def _join_peers(self, tools: list[Tool]) -> None:
        """After a rebuild the built-ins get a fresh peer map; rejoin it."""
        peers = self._policy_session.peer_tools
        for tool in tools:
            peers[tool.name] = tool
            tool._peer_tools = peers  # type: ignore[attr-defined]

    def _snapshot(self, live: _Live) -> ConnectorSnapshot:
        return self._snapshot_of(
            live.config, live.status, live.specs, live.error, live.retry_in_s
        )

    @staticmethod
    def _snapshot_of(
        config: ConnectorConfig,
        status: ConnectorStatus,
        specs: list[RemoteToolSpec],
        error: str | None = None,
        retry_in_s: int | None = None,
    ) -> ConnectorSnapshot:
        return ConnectorSnapshot(
            name=config.name,
            title=config.display_title,
            url=config.url,
            enabled=config.enabled,
            status=status,
            auth=config.auth,
            trust_read_only_hints=config.trust_read_only_hints,
            tools=tool_infos(config, specs),
            error=error,
            retry_in_s=retry_in_s,
        )
