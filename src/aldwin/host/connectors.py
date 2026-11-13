"""Connector lifecycle in the host: background start, reconnects and tools on demand.

Connections reconnect with backoff and follow tool-list changes.

`ConnectorAdmin` changes config and credentials. `ConnectorManager` keeps one
live connection per enabled connector, indexes every usable tool, and offers
the model only `find_connector_tools` plus the tools it has loaded through it.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import shlex
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agentling import AgentSession, Tool

from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.connectors.catalog import CatalogEntry, load_catalog
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
    NeedsSignInCaller,
    build_auth,
    probe_connector,
)
from aldwin.core.connectors.credentials import (
    CredentialStore,
    FileCredentialStore,
    bearer_credential,
    credential_identity,
)
from aldwin.core.connectors.errors import (
    ConnectorAuthRejected,
    ConnectorAuthRequired,
    ConnectorConfigError,
    ConnectorError,
    ConnectorNotApproved,
    NeedsSignIn,
    SignInCancelled,
    SignInError,
    SignInTimedOut,
)
from aldwin.core.connectors.icons import (
    Icon,
    icon_path,
    icon_version,
    remove_icon,
    save_icon,
)
from aldwin.core.connectors.importer import (
    FoundConfig,
    ImportResult,
    apply_import,
    find_config_files,
    load_file,
    plan_import,
)
from aldwin.core.connectors.index import IndexedTool, ToolIndex
from aldwin.core.connectors.local import (
    ADD_TOOL,
    ENABLE_TOOL,
    LaunchSpec,
    check_command_floor,
    is_approved,
    launch_spec,
    local_approval_request,
    require_command,
)
from aldwin.core.connectors.mcp_tool import McpTool, RemoteToolSpec, ToolCaller
from aldwin.core.connectors.oauth_messages import (
    DEFAULT_CLIENT_METADATA_URL,
    SIGN_IN_TIMEOUT_S,
    needs_sign_in,
    needs_sign_in_notice,
    no_sign_in_needed,
    nothing_waiting,
)
from aldwin.core.connectors.picks import PickDrop, resolve_picked_apps
from aldwin.core.connectors.policy import connector_policy
from aldwin.core.connectors.proposal import (
    ConnectorProposal,
    ProposeOutcome,
    resolve_proposal,
)
from aldwin.core.connectors.state import (
    ConnectorSnapshot,
    ConnectorStatus,
    format_connectors_block,
    tool_infos,
)
from aldwin.core.policy import (
    ApprovalAnswer,
    ApprovalPreview,
    ApprovalRequest,
    PolicyAskFn,
    PolicySession,
    register_runtime_policy,
    unregister_runtime_policy,
    wrap_tools_with_policy,
)
from aldwin.core.tools.connector_tools import (
    LoadResult,
    build_find_connector_tools_tool,
    build_propose_connector_tool,
)
from aldwin.core.workspace import default_workspace_root

logger = logging.getLogger("aldwin.host.connectors")

BACKOFF_START_S = 30.0
BACKOFF_MAX_S = 600.0

if TYPE_CHECKING:
    # Type-only: importing sign-in at runtime would load `mcp` at startup.
    from aldwin.core.connectors.sign_in import SignedIn, SignInWait

ConnectionFactory = Callable[..., McpConnection]

# OpenAI rejects a request that offers the model more than 128 tools; the
# loaded-tool cap keeps every turn well under that.
MAX_MODEL_TOOLS = 128
DEFAULT_LOADED_CAP = 40
_CONNECTOR_TOOL_PREFIX = "mcp__"
AuthMode = Literal["browser", "paste", "token"]
SSE_UNSUPPORTED = "SSE is not supported"
PROPOSE_TOOL = "propose_connector"
ADDS_CONNECTOR = "adds an external connector"
CUSTOM_SERVER = "custom server, not in the catalogue"
_SIGN_IN_HOW: dict[str, str] = {
    "oauth": "sign in with your browser",
    "bearer": "needs a personal access token",
}
NEEDS_APPROVAL = "needs approval: open /connectors to enable it"
WINDOWS_UNSUPPORTED = "local servers are not supported on Windows"


def _text_arg(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _approve_if_unchanged(launch_digest: str | None) -> PolicyAskFn:
    """Reuse the yes given in `propose_connector` for the add flow's own question.

    It applies only to the exact command the user was shown.
    """

    async def approve(request: ApprovalRequest) -> ApprovalAnswer:
        if launch_digest is not None and request.digest == launch_digest:
            return ApprovalAnswer("yes", "user", "approved in propose_connector")
        return ApprovalAnswer("no", "user", "the command changed after approval")

    return approve


def _local_servers_supported() -> bool:
    # Process groups and killpg are POSIX-only.
    return sys.platform != "win32"


@dataclass(frozen=True)
class AuthPrompt:
    """A flow needs the user; the host turns this into a `connector.auth` event."""

    name: str
    title: str
    mode: AuthMode
    url: str | None
    token_help_url: str | None
    expires_in: int


OnAuth = Callable[[AuthPrompt], Awaitable[None]]
IconFinder = Callable[
    [ConnectorConfig, Sequence[tuple[str, str | None]]], Awaitable[Icon | None]
]
# The most a first connection waits for the app's icon.
ICON_BUDGET_S = 5.0


@dataclass
class _AuthWait:
    """One flow waiting for the user: a sign-in (browser or paste) or a token."""

    mode: AuthMode
    sign_in: SignInWait | None = None
    token: asyncio.Future[str] | None = None


@dataclass(frozen=True)
class ConnectorEvent:
    """Something a door should hear about: a status change or a one-line notice."""

    kind: Literal["status", "notice"]
    snapshot: ConnectorSnapshot | None = None
    message: str = ""


@dataclass(frozen=True)
class ImportReport:
    """What an import found, what it saved, and what went wrong on the way."""

    found: list[FoundConfig]
    results: list[ImportResult]
    warnings: list[str]


@dataclass(frozen=True)
class AddResult:
    """What adding (or probing) a connector found."""

    config: ConnectorConfig
    snapshot: ConnectorSnapshot
    notice: str | None = None

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
    """Config, credentials and checks, with no live session; shell commands use it."""

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

    def launch_spec(
        self, config: ConnectorConfig, *, workspace_root: Path
    ) -> LaunchSpec:
        """How `config` would start: command floor, folder and environment checked."""
        return launch_spec(
            config,
            workspace_root=workspace_root,
            home=self.home,
            environ=self._environ,
            credentials=self.store,
        )

    async def approve_local(
        self,
        config: ConnectorConfig,
        *,
        tool: str,
        workspace_root: Path,
        approve: PolicyAskFn | None,
        approval_log: ApprovalLogger | None,
    ) -> ConnectorConfig:
        """Ask before a local command first runs and log the answer.

        Returns `config` carrying its approval digest. Starts and saves nothing;
        a `no` (or nobody to ask) raises `ConnectorNotApproved`.
        """
        target = shlex.join([config.command or "", *config.args])
        try:
            check_command_floor([config.command or "", *config.args])
        except ConnectorConfigError as exc:
            if approval_log is not None:
                approval_log.log_decision(
                    call_id=uuid.uuid4().hex,
                    tool=tool,
                    target=target,
                    digest=None,
                    tier="ask",
                    decision="blocked",
                    answer="none",
                    source="rule",
                    reason=exc.message,
                    connector=config.name,
                )
            raise
        spec = self.launch_spec(config, workspace_root=workspace_root)
        require_command(spec)
        request = local_approval_request(
            spec, config, tool=tool, workspace_root=workspace_root
        )
        answer = (
            await approve(request)
            if approve is not None
            else ApprovalAnswer("no", "no_approver")
        )
        if approval_log is not None:
            approval_log.log_decision(
                # The approval id ties this line to the event the door answered.
                call_id=answer.approval_id or uuid.uuid4().hex,
                tool=tool,
                target=target,
                digest=request.digest,
                tier="ask",
                decision="asked",
                answer=answer.answer,
                source=answer.source,
                reason=answer.reason,
                connector=config.name,
                labels=request.risky,
            )
        if answer.answer == "no":
            raise ConnectorNotApproved(config.display_title)
        return config.model_copy(update={"approved_digest": request.digest})

    def import_file(
        self,
        path: Path,
        *,
        rename_conflicts: bool,
        workspace: Path | None,
        user_home: Path | None = None,
    ) -> ImportResult:
        """Save the servers another app's config file lists, starting nothing.

        Imported local commands, and everything from a project file, are saved disabled.
        """
        loaded = load_file(
            path, user_home=user_home or Path.home(), workspace=workspace
        )
        with connectors_locked(self.home):
            plan = plan_import(
                loaded,
                existing=self._entries_or_raise(),
                rename_conflicts=rename_conflicts,
            )
            result = apply_import(plan, home=self.home, credentials=self.store)
        return replace(result, app=loaded.app, warnings=loaded.warnings)

    def check_new(self, config: ConnectorConfig) -> None:
        """Raise `ConnectorConfigError` for a bad URL or a taken name."""
        if config.url is not None:
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
        """Read, change and save one entry under one lock, so no change is lost."""
        with connectors_locked(self.home):
            entries = dict(self._entries_or_raise())
            current = self._existing(entries, name)
            updated = ConnectorConfig.model_validate(
                {**current.model_dump(), **changes(current)}
            )
            entries[name] = updated
            save_connectors(self.home, entries)
            return updated

    def sign_out(self, name: str) -> None:
        """Forget a connector's sign-in (local only; nothing is revoked)."""
        self.store.delete(name)

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
    lost_error: ConnectorError | None = None
    supervisor: asyncio.Task[None] | None = None
    # Looks for the app's own icon after it connects; stopped with the connector.
    icon_task: asyncio.Task[None] | None = None


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
        loaded_cap: int = DEFAULT_LOADED_CAP,
        workspace_root: Callable[[], Path] | None = None,
        turn_auth: OnAuth | None = None,
        find_icon: IconFinder | None = None,
    ) -> None:
        self.admin = admin
        self._find_icon = find_icon
        # Connectors already asked for an icon in this run; a miss is not retried.
        self._icons_tried: set[str] = set()
        # How a proposal made during a turn reaches the user to sign in.
        self._turn_auth = turn_auth
        self._catalog: tuple[CatalogEntry, ...] | None = None
        # Local servers run in the session's workspace, read when each one starts.
        self._workspace_root = workspace_root or (
            lambda: default_workspace_root(admin.home)
        )
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
        self._cimd_url = DEFAULT_CLIENT_METADATA_URL
        self._auth_waits: dict[str, _AuthWait] = {}
        self._turn_notices: list[str] = []
        self._index = ToolIndex()
        self._loaded_cap = loaded_cap
        # Loaded tools by model name: the connector and the tick last used at.
        self._loaded: dict[str, tuple[str, int]] = {}
        self._tick = itertools.count()
        self._finder, self._proposer = wrap_tools_with_policy(
            [
                build_find_connector_tools_tool(index=self._index, load=self.load),
                build_propose_connector_tool(
                    resolve=self.resolve_proposal,
                    preview=self.preview_proposal,
                    propose=self.propose,
                ),
            ],
            session=policy_session,
            ask=ask,
            approval_log=approval_log,
            join=True,
        )

    async def start(self) -> None:
        """Start every enabled connector in the background and return at once."""
        load = self.admin.load()
        self.load_error = load.error
        self._cimd_url = load.oauth_client_metadata_url
        self._configs = dict(load.entries)
        self._fingerprint = connectors_fingerprint(self.admin.home)
        for config in self._configs.values():
            if config.enabled:
                await self._begin(config)

    def attach(self, session: AgentSession) -> None:
        """Use `session` from now on; the loaded set carries over to it."""
        self._session = session
        self._join_peers(
            [
                self._finder,
                self._proposer,
                *(t for live in self._live.values() for t in live.tools.values()),
            ]
        )
        self._offer()

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
        self._cimd_url = load.oauth_client_metadata_url
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
                await self._begin(config)
                continue
            identity = credential_identity(self.admin.store.get(name))
            if config != live.config or identity != live.identity:
                await self._stop_live(name)
                await self._begin(config)

    async def aclose(self, *, timeout: float = 5.0) -> None:
        """Stop every connector; a session that hangs past `timeout` is abandoned."""
        names = list(self._live)
        await asyncio.gather(
            *(self._stop_live(n, close_timeout=timeout) for n in names),
            return_exceptions=True,
        )
        # A close that timed out abandons its task, and a native cancel can beat
        # the transport's shielded stop; kill any group still running.
        stdio = sys.modules.get("aldwin.core.connectors.stdio")
        if stdio is not None:
            stdio.kill_live_groups()

    async def add(
        self,
        config: ConnectorConfig,
        *,
        token: str | None,
        dry_run: bool,
        no_browser: bool = False,
        on_auth: OnAuth | None = None,
        approve: PolicyAskFn | None = None,
    ) -> AddResult:
        """Probe (`dry_run`) or connect and save. Nothing is saved on failure.

        An `oauth` connector signs in through the browser; a `bearer` one with no
        token asks for it through `on_auth` (token mode). A server added with no
        sign-in that answers 401 gets whichever it supports: browser sign-in if
        it publishes OAuth metadata, otherwise a token. A local command runs only
        after `approve` says yes.
        """
        self.admin.check_new(config)
        if config.kind == "local":
            return await self._add_local(config, dry_run=dry_run, approve=approve)
        if config.auth == "oauth":
            if dry_run:
                raise ConnectorConfigError(
                    f"{config.display_title} signs in through the browser, so it "
                    "can't be checked before adding. Add it to sign in."
                )
            return await self._add_with_sign_in(config, no_browser, on_auth)
        if dry_run:
            specs = await self._probe(config, token)
            return AddResult(config, self._snapshot_of(config, "connected", specs))

        live = _Live(config=config)
        await self._emit(ConnectorEvent("status", self._snapshot(live)))
        while True:
            connection = self._new_connection(live, self.admin.auth_for(config, token))
            try:
                specs = await connection.open()
                break
            except ConnectorAuthRequired:
                if on_auth is None or token is not None:
                    raise
                if config.auth == "none":
                    if await self._offers_browser_sign_in(config):
                        signed_in = config.model_copy(update={"auth": "oauth"})
                        return await self._add_with_sign_in(
                            signed_in, no_browser, on_auth
                        )
                    config = config.model_copy(update={"auth": "bearer"})
                    live.config = config
                elif config.auth != "bearer":
                    raise
                token = await self._ask_token(config, on_auth)
        try:
            self.admin.save_new(config, token)
        except ConnectorError:
            await connection.close()
            raise
        await self._adopt(live, connection, specs)
        return AddResult(config, self._snapshot(live))

    async def _add_local(
        self, config: ConnectorConfig, *, dry_run: bool, approve: PolicyAskFn | None
    ) -> AddResult:
        """Check a local command, or ask, start it to list its tools and save it.

        A dry run only checks; it never starts the program or asks.
        """
        if not _local_servers_supported():
            raise ConnectorConfigError(WINDOWS_UNSUPPORTED)
        root = self._workspace_root()
        if dry_run:
            self.admin.launch_spec(config, workspace_root=root)
            return AddResult(config, self._snapshot_of(config, "connected", []))
        approved = await self.admin.approve_local(
            config,
            tool=ADD_TOOL,
            workspace_root=root,
            approve=approve,
            approval_log=self._approval_log,
        )
        live = _Live(config=approved)
        await self._emit(ConnectorEvent("status", self._snapshot(live)))
        connection = self._new_connection(live, None)
        specs = await connection.open()
        try:
            self.admin.save_new(approved, None)
        except ConnectorError:
            await connection.close()
            raise
        await self._adopt(live, connection, specs)
        return AddResult(approved, self._snapshot(live))

    async def _add_with_sign_in(
        self, config: ConnectorConfig, no_browser: bool, on_auth: OnAuth | None
    ) -> AddResult:
        if on_auth is None:
            raise NeedsSignIn(needs_sign_in(config.display_title))
        live = _Live(config=config)
        await self._emit(ConnectorEvent("status", self._snapshot(live)))
        signed = await self._sign_in(live, no_browser, on_auth)
        if signed is None:
            plain = config.model_copy(update={"auth": "none", "oauth": None})
            added = await self.add(plain, token=None, dry_run=False)
            return replace(added, notice=no_sign_in_needed(config.display_title))
        try:
            self.admin.save_new(config, None)
        except ConnectorError:
            await signed.connection.close()
            self.admin.sign_out(config.name)
            raise
        await self._adopt(live, signed.connection, signed.specs)
        return AddResult(config, self._snapshot(live))

    async def _adopt(
        self, live: _Live, connection: McpConnection, specs: list[RemoteToolSpec]
    ) -> None:
        """Make a freshly connected, freshly saved connector live."""
        name = live.config.name
        self._configs[name] = live.config
        self._fingerprint = connectors_fingerprint(self.admin.home)
        live.identity = credential_identity(self.admin.store.get(name))
        self._live[name] = live
        await self._on_connected(live, connection, specs)
        self._spawn_supervisor(live)

    async def login(
        self, name: str, *, no_browser: bool = False, on_auth: OnAuth
    ) -> ConnectorSnapshot:
        """Sign an `oauth` connector in again."""
        config = self._require(name)
        if config.auth != "oauth":
            raise ConnectorConfigError(
                f"{config.display_title} doesn't use browser sign-in."
            )
        if not config.enabled:
            raise ConnectorConfigError(
                f"{config.display_title} is turned off. Enable it first."
            )
        previous = self._live.get(name)
        specs = list(previous.specs) if previous is not None else []
        await self._stop_live(name)
        live = _Live(config=config, specs=specs)
        self._live[name] = live
        await self._emit(ConnectorEvent("status", self._snapshot(live)))
        try:
            signed = await self._sign_in(live, no_browser, on_auth)
        except BaseException:
            if self._live.get(name) is live:
                await self._to_needs_auth(live, notice=False)
            raise
        if signed is None:
            config = self.admin.update(name, auth="none")
            self._configs[name] = config
            await self._stop_live(name)
            return self._snapshot(await self._connect_now(config))
        live.identity = credential_identity(self.admin.store.get(name))
        self._fingerprint = connectors_fingerprint(self.admin.home)
        await self._on_connected(live, signed.connection, signed.specs)
        self._spawn_supervisor(live)
        return self._snapshot(live)

    async def sign_out(self, name: str) -> ConnectorSnapshot:
        """Delete the sign-in; the tools stay and say to sign in again."""
        config = self._require(name)
        if config.auth != "oauth":
            raise ConnectorConfigError(
                f"{config.display_title} doesn't use browser sign-in."
            )
        previous = self._live.get(name)
        specs = list(previous.specs) if previous is not None else []
        await self._stop_live(name)
        self.admin.sign_out(name)
        self._fingerprint = connectors_fingerprint(self.admin.home)
        live = _Live(config=config, specs=specs)
        self._live[name] = live
        await self._to_needs_auth(live, notice=False)
        return self._snapshot(live)

    def auth_paste(self, name: str, redirect_url: str) -> None:
        """Hand a pasted address to a waiting sign-in."""
        wait = self._auth_waits.get(name)
        if wait is None or wait.sign_in is None:
            raise ConnectorConfigError(nothing_waiting(name))
        wait.sign_in.paste(redirect_url)

    def auth_token(self, name: str, token: str) -> None:
        """Hand a token to a flow waiting in token mode."""
        wait = self._auth_waits.get(name)
        if wait is None or wait.token is None or wait.token.done():
            raise ConnectorConfigError(nothing_waiting(name))
        wait.token.set_result(token)

    def waiting_for_user(self) -> set[str]:
        """Connectors whose flow is waiting on the user (browser, paste or token)."""
        return set(self._auth_waits)

    def take_turn_notices(self) -> list[str]:
        """Each connector that newly needs sign-in, once."""
        notices, self._turn_notices = self._turn_notices, []
        return notices

    def load(self, tools: Sequence[IndexedTool]) -> LoadResult:
        """Offer `tools` from the model's next call on, for `find_connector_tools`.

        Over the cap, the least recently used loaded tools go first.
        """
        available = self._available()
        new: list[IndexedTool] = []
        for tool in tools:
            if tool.model_name not in available:
                continue
            if tool.model_name in self._loaded:
                self.note_tool_used(tool.model_name)
            else:
                new.append(tool)
        cap = self._cap()
        new, skipped = new[:cap], new[cap:]
        evicted = self._evict(keep=cap - len(new))
        for tool in new:
            self._loaded[tool.model_name] = (tool.connector, next(self._tick))
        self._offer()
        loaded = [t for t in tools if t.model_name in self._loaded]
        return LoadResult(loaded=loaded, evicted=evicted, skipped=skipped)

    def pick_apps(
        self, names: Sequence[str]
    ) -> tuple[list[tuple[str, str]], list[tuple[str, PickDrop]]]:
        """Split picked apps into usable `(name, title)` pairs and the ones dropped.

        Only connectors that are connected and offer tools are usable.
        """
        connected = {
            name: live.config.display_title
            for name, live in self._live.items()
            if live.tools
        }
        return resolve_picked_apps(names, connected)

    def note_tool_used(self, model_name: str) -> None:
        """Keep a tool the model just called away from the eviction end."""
        entry = self._loaded.get(model_name)
        if entry is not None:
            self._loaded[model_name] = (entry[0], next(self._tick))

    def reset_loaded(self) -> None:
        """A new conversation starts with no connector tools loaded."""
        self._loaded.clear()
        self._offer()

    def _available(self) -> dict[str, Tool]:
        return {n: t for live in self._live.values() for n, t in live.tools.items()}

    def _cap(self) -> int:
        """Return the configured tool cap, lowered when built-ins leave less room."""
        session = self._session
        others = 0
        if session is not None:
            others = sum(
                1 for n in session.tools if not n.startswith(_CONNECTOR_TOOL_PREFIX)
            )
            others += sum(
                1 for t in (self._finder, self._proposer) if t.name not in session.tools
            )
        return max(0, min(self._loaded_cap, MAX_MODEL_TOOLS - others))

    def _evict(self, *, keep: int) -> list[str]:
        evicted: list[str] = []
        while len(self._loaded) > keep:
            oldest = min(self._loaded, key=lambda name: self._loaded[name][1])
            del self._loaded[oldest]
            evicted.append(oldest)
        return evicted

    def _offer(self) -> None:
        """Make the session's connector tools exactly the finder plus the loaded set.

        A loaded tool keeps its place while its connector is reconnecting, so a
        dropped connection does not make the model find everything again.
        """
        session = self._session
        if session is None:
            return
        available = self._available()
        for name, (connector, _tick) in list(self._loaded.items()):
            live = self._live.get(connector)
            reconnecting = live is not None and not live.tools
            if name not in available and not reconnecting:
                del self._loaded[name]
        self._evict(keep=self._cap())
        wanted = {name: available[name] for name in self._loaded if name in available}
        wanted[self._finder.name] = self._finder
        wanted[self._proposer.name] = self._proposer
        stale = [
            n
            for n in session.tools
            if n.startswith(_CONNECTOR_TOOL_PREFIX) and n not in wanted
        ]
        session.add_tools(wanted.values(), replace=True)
        if stale:
            session.remove_tools(stale)

    async def _sign_in(
        self, live: _Live, no_browser: bool, on_auth: OnAuth
    ) -> SignedIn | None:
        from aldwin.core.connectors.sign_in import SignInWait, sign_in

        config = live.config
        mode: AuthMode = "paste" if no_browser else "browser"
        wait = SignInWait(config.name)
        self._auth_waits[config.name] = _AuthWait(mode, sign_in=wait)

        async def show(url: str) -> None:
            await on_auth(
                AuthPrompt(
                    name=config.name,
                    title=config.display_title,
                    mode=mode,
                    url=url,
                    token_help_url=None,
                    expires_in=int(SIGN_IN_TIMEOUT_S),
                )
            )

        try:
            return await sign_in(
                config,
                self.admin.store,
                cimd_url=self._cimd_url,
                no_browser=no_browser,
                wait=wait,
                on_auth_url=show,
                connect=lambda provider: self._new_connection(live, provider),
            )
        finally:
            self._auth_waits.pop(config.name, None)

    async def _offers_browser_sign_in(self, config: ConnectorConfig) -> bool:
        """Whether a server that answered 401 publishes OAuth sign-in metadata."""
        from aldwin.core.connectors.oauth import offers_sign_in

        return await offers_sign_in(config)

    async def _ask_token(self, config: ConnectorConfig, on_auth: OnAuth) -> str:
        """Token mode: ask the user for a token and wait for `auth_token`."""
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._auth_waits[config.name] = _AuthWait("token", token=future)
        entry = next(
            (e for e in self._catalog_entries() if e.name == config.name), None
        )
        try:
            await on_auth(
                AuthPrompt(
                    name=config.name,
                    title=config.display_title,
                    mode="token",
                    url=None,
                    token_help_url=entry.token_help_url if entry else None,
                    expires_in=int(SIGN_IN_TIMEOUT_S),
                )
            )
            try:
                async with asyncio.timeout(SIGN_IN_TIMEOUT_S):
                    return await future
            except TimeoutError:
                raise SignInTimedOut() from None
        finally:
            self._auth_waits.pop(config.name, None)

    async def import_from_apps(
        self,
        *,
        paths: Sequence[str],
        dry_run: bool,
        rename_conflicts: bool,
        extra_paths: Sequence[str],
    ) -> ImportReport:
        """Find other apps' config files and, unless `dry_run`, import them.

        Nothing is started: imported local commands are saved disabled. One
        file that fails becomes a warning and the others still import.
        """
        root = self._workspace_root()
        found, warnings = find_config_files(
            user_home=Path.home(), workspace=root, extra_paths=extra_paths
        )
        if dry_run:
            return ImportReport(found, [], warnings)
        chosen = [Path(p) for p in paths] or [
            f.path for f in found if f.error is None and f.servers
        ]
        results: list[ImportResult] = []
        for path in chosen:
            try:
                results.append(
                    self.admin.import_file(
                        path, rename_conflicts=rename_conflicts, workspace=root
                    )
                )
            except ConnectorError as exc:
                warnings.append(exc.message)
            except Exception as exc:  # one bad file must not stop the others
                logger.exception("import: couldn't import %s", path)
                warnings.append(f"couldn't import {path} ({type(exc).__name__})")
        await self.reconcile()
        return ImportReport(found, results, warnings)

    async def enable(
        self, name: str, *, approve: PolicyAskFn | None = None
    ) -> ConnectorSnapshot:
        """Turn a connector on; a local one not yet approved asks first.

        That covers imported commands and ones edited by hand. It stays off on `no`.
        """
        current = self._require(name)
        if current.kind == "local" and not is_approved(current):
            approved = await self.admin.approve_local(
                current,
                tool=ENABLE_TOOL,
                workspace_root=self._workspace_root(),
                approve=approve,
                approval_log=self._approval_log,
            )
            # The approval answers the import's "review and enable" note.
            config = self.admin.update(
                name,
                enabled=True,
                approved_digest=approved.approved_digest,
                note=None,
            )
        else:
            config = self.admin.update(name, enabled=True)
        self._configs[name] = config
        self._fingerprint = connectors_fingerprint(self.admin.home)
        await self._stop_live(name)
        if self._needs_sign_in_at_start(config):
            return self._snapshot(await self._begin(config))
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
        remove_icon(self.admin.home, name)
        self._icons_tried.discard(name)
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

    def snapshots(self) -> list[ConnectorSnapshot]:
        out: list[ConnectorSnapshot] = []
        for name, config in sorted(self._configs.items()):
            live = self._live.get(name)
            if live is not None:
                out.append(self._snapshot(live))
            else:
                out.append(self._with_icon(self._snapshot_of(config, "disabled", [])))
        return out

    def context_block(self) -> str:
        available = [
            e.name for e in self._catalog_entries() if e.name not in self._configs
        ]
        saved_off = [c.name for c in self._configs.values() if not c.enabled]
        return format_connectors_block(
            self.snapshots(), available=available, saved_off=saved_off
        )

    def resolve_proposal(self, args: dict[str, Any]) -> ConnectorProposal:
        """The connector `propose_connector`'s arguments point at."""
        return resolve_proposal(
            name=_text_arg(args, "name"),
            url=_text_arg(args, "url"),
            command=_text_arg(args, "command"),
            args=args.get("args") or None,
            catalog=self._catalog_entries(),
            configs=self._configs,
        )

    def preview_proposal(
        self, proposal: ConnectorProposal
    ) -> tuple[ApprovalPreview, str | None]:
        """What the user approves, and the launch digest of a local command.

        A local command is checked here (the command floor, the folder, that the
        program exists) so a doomed proposal never reaches the panel.
        """
        config = proposal.config
        enabling = proposal.kind == "enable"
        if config.kind == "local":
            root = self._workspace_root()
            spec = self.admin.launch_spec(config, workspace_root=root)
            require_command(spec)
            request = local_approval_request(
                spec, config, tool=PROPOSE_TOOL, workspace_root=root
            )
            verb = "turn on" if enabling else "run"
            preview = ApprovalPreview(
                summary=f"Aldwin wants to {verb} a local connector: {config.name}",
                detail=request.detail,
                risky=(ADDS_CONNECTOR, *request.risky),
            )
            return preview, request.digest
        how = _SIGN_IN_HOW.get(config.auth, "")
        detail = f"{config.server_url} · {how}" if how else config.server_url
        risky: tuple[str, ...] = (ADDS_CONNECTOR,)
        if enabling:
            summary = f"Aldwin wants to turn on the {config.display_title} connector"
        elif proposal.kind == "catalog":
            summary = f"Aldwin wants to add the {config.display_title} connector"
        else:
            summary = f"Aldwin wants to add a connector: {config.name}"
            detail += "\nIts tools will ask before running."
            risky = (ADDS_CONNECTOR, CUSTOM_SERVER)
        return ApprovalPreview(summary=summary, detail=detail, risky=risky), None

    async def propose(
        self, proposal: ConnectorProposal, launch_digest: str | None
    ) -> ProposeOutcome:
        """Run an approved proposal through the `/connectors` flows and load its tools.

        Loading them at once lets the current turn use them.
        """
        name = proposal.name
        if name in self._auth_waits:
            return ProposeOutcome("failed", name, reason=f"{name} is busy")
        approve = _approve_if_unchanged(launch_digest)
        try:
            if proposal.kind == "enable":
                snapshot = await self.enable(name, approve=approve)
                if snapshot.status == "needs_auth" and self._turn_auth is not None:
                    snapshot = await self.login(name, on_auth=self._turn_auth)
            else:
                added = await self.add(
                    proposal.config,
                    token=None,
                    dry_run=False,
                    on_auth=self._turn_auth,
                    approve=approve,
                )
                snapshot = added.snapshot
        except ConnectorNotApproved:
            return ProposeOutcome("changed", name)
        except SignInCancelled:
            return ProposeOutcome("cancelled", name)
        except SignInTimedOut:
            return ProposeOutcome("sign_in_failed", name, reason="timed out")
        except SignInError as exc:
            return ProposeOutcome("sign_in_failed", name, reason=exc.message)
        except ConnectorError as exc:
            return ProposeOutcome("failed", name, reason=exc.message)
        if snapshot.status != "connected":
            reason = snapshot.error or snapshot.status.replace("_", " ")
            return ProposeOutcome("failed", name, reason=reason)
        indexed = [
            tool
            for tool_name in self._index.names(name)
            if (tool := self._index.get(name, tool_name)) is not None
        ]
        result = self.load(indexed)
        return ProposeOutcome(
            "connected", name, tools=len(indexed), loaded=len(result.loaded)
        )

    def _catalog_entries(self) -> tuple[CatalogEntry, ...]:
        if self._catalog is None:
            try:
                self._catalog = load_catalog()
            except Exception:  # a broken catalogue must not stop the session
                logger.exception("connector catalogue could not be loaded")
                self._catalog = ()
        return self._catalog

    def labels(self, model_name: str) -> tuple[str | None, str | None]:
        """`(connector title, server tool name)` for a connector tool, else Nones."""
        for live in self._live.values():
            tool = live.tools.get(model_name)
            if tool is not None:
                inner = getattr(tool, "inner_tool", tool)
                if isinstance(inner, McpTool):
                    return inner.connector_title, inner.remote_name
        return None, None

    def _require(self, name: str) -> ConnectorConfig:
        if name not in self._configs:
            raise ConnectorConfigError(f"No connector named {name}.")
        return self._configs[name]

    def _needs_sign_in_at_start(self, config: ConnectorConfig) -> bool:
        return config.auth == "oauth" and self.admin.store.get(config.name) is None

    def _blocked_reason(self, config: ConnectorConfig) -> str | None:
        """Why `config` must not connect at all, short of the user acting."""
        if config.transport == "sse":
            return SSE_UNSUPPORTED
        if config.kind == "local":
            if not _local_servers_supported():
                return WINDOWS_UNSUPPORTED
            if not is_approved(config):
                return NEEDS_APPROVAL
        return None

    async def _hold(self, config: ConnectorConfig, reason: str) -> _Live:
        """Show `config` as failed without connecting or retrying."""
        live = _Live(config=config, status="failed", error=reason)
        self._live[config.name] = live
        await self._emit(ConnectorEvent("status", self._snapshot(live)))
        return live

    async def _begin(self, config: ConnectorConfig) -> _Live:
        """Start a connector; an `oauth` one never signed in waits for the user."""
        reason = self._blocked_reason(config)
        if reason is not None:
            return await self._hold(config, reason)
        if self._needs_sign_in_at_start(config):
            live = _Live(config=config)
            self._live[config.name] = live
            await self._to_needs_auth(live, notice=True)
            return live
        return self._start_live(config)

    def _start_live(self, config: ConnectorConfig) -> _Live:
        live = _Live(
            config=config,
            identity=credential_identity(self.admin.store.get(config.name)),
        )
        self._live[config.name] = live
        self._spawn_supervisor(live)
        return live

    def _needs_person(self, live: _Live, error: ConnectorError | None) -> bool:
        if isinstance(error, NeedsSignIn):
            return True
        return live.config.auth == "oauth" and isinstance(
            error, ConnectorAuthRejected | ConnectorAuthRequired
        )

    async def _to_needs_auth(self, live: _Live, *, notice: bool) -> None:
        """Stop connecting: only the user can fix this. The tools stay and say so."""
        connection, live.connection = live.connection, None
        if connection is not None:
            await connection.close()
        live.status, live.error, live.retry_in_s = "needs_auth", None, None
        if live.specs:
            self._apply(
                live, live.specs, caller=NeedsSignInCaller(live.config.display_title)
            )
        if notice:
            self._turn_notices.append(needs_sign_in_notice(live.config.display_title))
        await self._emit(ConnectorEvent("status", self._snapshot(live)))

    async def _connect_now(self, config: ConnectorConfig) -> _Live:
        """Try to connect now; if that fails, keep retrying in the background."""
        reason = self._blocked_reason(config)
        if reason is not None:
            return await self._hold(config, reason)
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
        """Log an unexpected supervisor exit and show the connector as failed.

        A supervisor only ends by cancellation or by being stopped.
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
                    if live.status == "needs_auth":
                        return
                    await self._sleep(delay)
                    delay = min(delay * 2, BACKOFF_MAX_S)
                    continue
                delay = BACKOFF_START_S
            await live.lost.wait()
            live.lost.clear()
            if self._needs_person(live, live.lost_error):
                self._remove_tools(live)
                await self._to_needs_auth(live, notice=True)
                return
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
            if self._needs_person(live, exc):
                await self._to_needs_auth(live, notice=True)
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
        if self._wants_icon(live.config) and live.icon_task is None:
            self._icons_tried.add(live.config.name)
            live.icon_task = asyncio.create_task(
                self._keep_icon(live, connection), name=f"icon:{live.config.name}"
            )
        if reconnected:
            await self._emit(
                ConnectorEvent(
                    "notice", message=f"{live.config.display_title} reconnected."
                )
            )

    def _wants_icon(self, config: ConnectorConfig) -> bool:
        """An app outside the catalogue, not yet tried this run, with no saved icon.

        Catalogue apps ship with their marks.
        """
        name = config.name
        if self._find_icon is None or name in self._icons_tried:
            return False
        if icon_path(self.admin.home, name) is not None:
            return False
        return not any(
            e.name == name and e.url == config.url for e in self._catalog_entries()
        )

    async def _keep_icon(self, live: _Live, connection: Any) -> None:
        """Find and save the app's icon, then announce it; never fails the connector."""
        config = live.config
        if self._find_icon is None:
            return
        declared = list(getattr(connection, "declared_icons", ()))
        try:
            async with asyncio.timeout(ICON_BUDGET_S):
                icon = await self._find_icon(config, declared)
        except Exception as exc:
            logger.debug("no icon for %s: %s", config.name, exc)
            return
        if icon is None or not self._tracked(live):
            return
        save_icon(self.admin.home, config.name, icon)
        await self._emit(ConnectorEvent("status", self._snapshot(live)))

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
                live.lost_error = error
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

        extra: dict[str, Any] = {}
        if live.config.kind == "local":
            extra["transport_factory"] = self._stdio_transport(live.config)
        connection = self._connection_factory(
            live.config,
            auth=auth,
            on_tools_changed=on_tools_changed,
            on_lost=on_lost,
            **extra,
        )
        holder.append(connection)
        return connection

    def _stdio_transport(self, config: ConnectorConfig) -> Callable[[Any], Any]:
        """Return a local server's transport, built lazily when the connection opens.

        A bad folder or a missing program then fails that attempt, not this call.
        """

        def build(_http: Any) -> Any:
            from aldwin.core.connectors.stderr_log import ServerLog
            from aldwin.core.connectors.stdio import StdioTransport

            spec = self.admin.launch_spec(config, workspace_root=self._workspace_root())
            return StdioTransport(spec, log=ServerLog(self.admin.home, config.name))

        return build

    def _apply(
        self,
        live: _Live,
        specs: list[RemoteToolSpec],
        *,
        caller: ToolCaller | None = None,
    ) -> None:
        """Make the session's tools match `specs` for this connector."""
        live.specs = list(specs)
        connection: ToolCaller | None = caller or live.connection
        assert connection is not None
        fresh: dict[str, Tool] = {}
        indexed: list[IndexedTool] = []
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
                connection=connection,
                tmp_dir=self.admin.home / "tmp",
            )
            indexed.append(
                IndexedTool(
                    connector=live.config.name,
                    title=live.config.display_title,
                    name=spec.name,
                    model_name=policy.name,
                    description=spec.description,
                    auto=policy.tier == "auto",
                )
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
        self._index.replace(live.config.name, indexed)
        self._offer()
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
        if live.icon_task is not None:
            live.icon_task.cancel()
            await asyncio.gather(live.icon_task, return_exceptions=True)
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
        self._index.drop(live.config.name)
        self._offer()
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
        return self._with_icon(
            self._snapshot_of(
                live.config, live.status, live.specs, live.error, live.retry_in_s
            )
        )

    def _with_icon(self, snapshot: ConnectorSnapshot) -> ConnectorSnapshot:
        return replace(snapshot, icon=icon_version(self.admin.home, snapshot.name))

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
            transport=(
                "sse"
                if config.transport == "sse"
                else "stdio"
                if config.kind == "local"
                else "http"
            ),
            command=(
                shlex.join([config.command, *config.args])
                if config.command is not None
                else None
            ),
            note=config.note,
        )
