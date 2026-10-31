"""In-process host bootstrap, setup/inspect bridge, and queue transport for Aldwin."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import SecretStr

from aldwin.core.home import (
    HomeError,
    ensure_home,
    format_home_path,
    home_dir,
    migrate_0_0_1,
)
from aldwin.core.logging import setup_logging
from aldwin.core.memory import (
    MemorySummary,
    NoteEntry,
    memory_summary,
    pop_profile_truncation_warning,
)
from aldwin.core.persona import PersonaSummary, persona_summary, persona_text
from aldwin.core.policy import sanitize_display_text
from aldwin.core.settings import (
    ConfigError,
    KeySource,
    SecretsConfig,
    load_config,
    mask_secret,
    resolve_key,
    save_secrets,
    update_config_model,
)
from aldwin.core.tools import ToolInfo, list_builtin_tools
from aldwin.core.update_check import update_nudge_line
from aldwin.core.workspace import (
    Workspace,
    WorkspaceError,
    WorkspaceNotAllowedError,
    WorkspaceNotFoundError,
    default_workspace_root,
    format_outside_display,
)

if TYPE_CHECKING:
    from aldwin.client.client import AldwinClient
    from aldwin.protocol import Event, Request

logger = logging.getLogger("aldwin.host")

NewConversationHandler = Callable[[], Awaitable[None]]
KeyCheckResult = Literal["ok", "invalid", "no_access", "network_error"]


@dataclass(frozen=True, slots=True)
class WorkspaceSummary:
    """Active workspace root and session-approved folders for `/workspace`."""

    root: Path
    display_root: str
    granted_folders: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OnboardingStatus:
    """Current API key and model configuration state for onboarding and `setup`."""

    has_openai_key: bool
    openai_source: KeySource
    masked_openai_key: str
    has_tavily_key: bool
    tavily_source: KeySource
    masked_tavily_key: str
    model: str


@dataclass(frozen=True, slots=True)
class SkillSummary:
    """One skill catalogue entry for `/skills` and `aldwin skills`."""

    name: str
    description: str
    source: str = "default"
    flags: tuple[str, ...] = ()
    last_used: str | None = None

    @property
    def error(self) -> str | None:
        """First `error: …` flag reason, if any."""
        for flag in self.flags:
            if flag.startswith("error:"):
                return flag.removeprefix("error:").strip()
        return None

    @property
    def tools_removed(self) -> bool:
        """True when the catalogue marked `tools removed`."""
        return "tools removed" in self.flags


@dataclass(frozen=True, slots=True)
class InvalidSkillSummary:
    """One invalid skill folder flagged for `/skills` (legacy alias)."""

    name: str
    display_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class SkillsReport:
    """Skill catalogue rows for `/skills` (errors included as rows with `error`)."""

    valid: tuple[SkillSummary, ...]
    invalid: tuple[InvalidSkillSummary, ...] = ()


def get_onboarding_status(home: Path | None = None) -> OnboardingStatus:
    """Inspect whether usable OpenAI/Tavily keys exist and return masked metadata."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    openai_res = resolve_key("openai", root)
    tavily_res = resolve_key("tavily", root)
    return OnboardingStatus(
        has_openai_key=bool(openai_res.key),
        openai_source=openai_res.source,
        masked_openai_key=mask_secret(openai_res.key),
        has_tavily_key=bool(tavily_res.key),
        tavily_source=tavily_res.source,
        masked_tavily_key=mask_secret(tavily_res.key),
        model=config.model,
    )


async def check_openai_key(api_key: str) -> KeyCheckResult:
    """Validate an OpenAI API key via `aldwin.core.models.check_key`."""
    from aldwin.core.models import KeyStatus, check_key

    status = await check_key(api_key)
    if status == KeyStatus.OK:
        return "ok"
    if status == KeyStatus.NO_ACCESS:
        return "no_access"
    if status == KeyStatus.NETWORK_ERROR:
        return "network_error"
    return "invalid"


async def verify_active_openai_key(home: Path | None = None) -> KeyCheckResult:
    """Validate the resolved OpenAI key for `home` without exposing it to doors."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    resolved = resolve_key("openai", root)
    key = (resolved.key or "").strip()
    if not key:
        return "invalid"
    return await check_openai_key(key)


async def list_available_models(
    api_key: str | None = None,
    home: Path | None = None,
) -> list[str]:
    """Return available OpenAI chat models for the active or supplied API key."""
    from aldwin.core.models import list_chat_models

    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    effective_key = (api_key or "").strip()
    if not effective_key:
        resolved = resolve_key("openai", root)
        effective_key = (resolved.key or "").strip()

    if not effective_key:
        return [config.model]

    try:
        models = await list_chat_models(effective_key)
    except Exception as exc:
        logger.warning("Failed to list OpenAI chat models: %s", exc)
        return [config.model]

    if not models:
        return [config.model]
    if config.model not in models:
        return [config.model, *models]
    return models


def save_setup(
    *,
    openai_api_key: str | None = None,
    tavily_api_key: str | None = None,
    model: str | None = None,
    theme: str | None = None,
    last_seen_version: str | None = None,
    home: Path | None = None,
) -> None:
    """Persist API keys, model, theme, and last_seen_version to the home folder."""
    from aldwin.core.settings import (
        update_config_last_seen_version,
        update_config_theme,
    )

    root = (home if home is not None else home_dir()).expanduser().resolve()
    ensure_home(root)

    openai_clean = (openai_api_key or "").strip()
    tavily_clean = (tavily_api_key or "").strip()
    if openai_clean or tavily_clean:
        secrets = SecretsConfig(
            openai_api_key=SecretStr(openai_clean) if openai_clean else None,
            tavily_api_key=SecretStr(tavily_clean) if tavily_clean else None,
        )
        save_secrets(secrets, root)

    model_clean = (model or "").strip()
    if model_clean:
        update_config_model(model_clean, root)

    theme_clean = (theme or "").strip()
    if theme_clean:
        update_config_theme(theme_clean, root)

    version_clean = (last_seen_version or "").strip()
    if version_clean:
        update_config_last_seen_version(version_clean, root)


def get_skills_report(home: Path | None = None) -> SkillsReport:
    """Build the skill catalogue (defaults + user) for `/skills`."""
    import aldwin
    from aldwin.core.settings import load_config
    from aldwin.core.skills import build_catalogue, to_catalog_items

    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    catalogue = build_catalogue(
        root,
        version=aldwin.__version__,
        model=config.model,
    )
    rows: list[SkillSummary] = []
    invalid: list[InvalidSkillSummary] = []
    for item in to_catalog_items(catalogue):
        raw_flags = item.get("flags") or ()
        if isinstance(raw_flags, list | tuple):
            flags = tuple(str(f) for f in raw_flags)
        else:
            flags = ()
        last = item.get("last_used")
        summary = SkillSummary(
            name=str(item["name"]),
            description=str(item.get("description") or ""),
            source=str(item.get("source") or "default"),
            flags=flags,
            last_used=str(last) if last else None,
        )
        rows.append(summary)
        if summary.error:
            invalid.append(
                InvalidSkillSummary(
                    name=summary.name,
                    display_path=format_home_path(root / "skills" / summary.name),
                    reason=summary.error,
                )
            )
    return SkillsReport(valid=tuple(rows), invalid=tuple(invalid))


def purge_home_data(home: Path | None = None) -> Path:
    """Delete the Aldwin home folder (`~/.aldwin` or `ALDWIN_HOME`) for `--purge`."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    if root.exists():
        shutil.rmtree(root)
    return root


def get_memory_summary(home: Path | None = None) -> MemorySummary:
    """Return profile and notes summary data for `/memory`."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    return memory_summary(root, config.limits)


def get_persona_text(home: Path | None = None) -> str:
    """Return the active `persona.md` text for `/persona`."""
    return persona_text(home)


def get_persona_summary(home: Path | None = None) -> PersonaSummary:
    """Return path, text, and cap metadata for `/persona`."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    return persona_summary(root, cap=config.limits.persona_chars)


def resolve_workspace_summary(
    new_root: Path | str,
    *,
    home: Path | None = None,
) -> WorkspaceSummary:
    """Validate `new_root` and return a `WorkspaceSummary`."""
    ws = Workspace(new_root, home=home)
    return WorkspaceSummary(
        root=ws.root,
        display_root=ws.display_root,
        granted_folders=(),
    )


def get_builtin_tools() -> list[ToolInfo]:
    """Return one-line descriptions of the built-in tools for `/tools`."""
    return list_builtin_tools()


class PlatformNotSupportedError(Exception):
    """Raised when Aldwin is launched on an unsupported operating system."""


class ClientBootstrapError(Exception):
    """Raised when the home folder or configuration cannot be initialised."""


@dataclass(frozen=True)
class BootstrapResult:
    """Summary returned to a door after the host initialises the home folder."""

    home: Path
    display_home: str
    created: bool
    model: str
    plain: bool
    workspace: Path = field(default_factory=lambda: Path.cwd().resolve())
    display_workspace: str = ""
    theme: str = "dark"

    def __post_init__(self) -> None:
        if not self.display_workspace:
            object.__setattr__(
                self,
                "display_workspace",
                format_outside_display(self.workspace),
            )


def update_available_nudge(
    home: Path,
    *,
    current_version: str,
    plain: bool = False,
) -> str | None:
    """Return a one-line PyPI update nudge, or ``None`` when none should show.

    Reads ``check_for_updates`` from ``config.yaml``. Failures and opt-out are
    silent. Does not run during ``bootstrap_host``; only when a door asks.
    """
    try:
        config = load_config(home)
    except ConfigError:
        return None
    return update_nudge_line(
        current_version=current_version,
        check_enabled=config.check_for_updates,
        plain=plain,
    )


def whats_new_due(home: Path, current_version: str) -> bool:
    """Return True when the upgrade What's new panel for `current_version` is due.

    Due when `last_seen_version` is missing, unparseable (shown once rather
    than silently suppressed forever), or behind `current_version` at the
    feature-release level (`major.minor.micro`). A patch release such as
    `0.0.2.1` never shows the `0.0.2` panel a second time.
    """
    try:
        config = load_config(home)
    except ConfigError:
        return False
    seen = (config.last_seen_version or "").strip()
    if not seen:
        return True
    from packaging.version import InvalidVersion, Version

    try:
        seen_release = Version(seen).release[:3]
        current_release = Version(current_version).release[:3]
    except InvalidVersion:
        return True
    return seen_release < current_release


def mark_whats_new_seen(home: Path, version: str) -> None:
    """Persist `last_seen_version` so the What's new panel does not show again."""
    from aldwin.core.settings import update_config_last_seen_version

    update_config_last_seen_version(version, home)


def bootstrap_host(
    *,
    plain: bool = False,
    debug: bool = False,
    workspace: Path | str | None = None,
) -> BootstrapResult:
    """Initialise the Aldwin home folder, rotating log, configuration, and workspace.

    Raises ``PlatformNotSupportedError`` on Windows, ``ClientBootstrapError``
    when ``config.yaml`` is invalid, and ``WorkspaceError`` when an explicit
    ``workspace`` path is missing or not allowed.
    """
    if sys.platform == "win32":
        raise PlatformNotSupportedError(
            "Aldwin does not support Windows yet. Please use macOS or Linux."
        )

    root = home_dir()
    if root.exists():
        setup_logging(root, debug=debug)
        created = ensure_home(root)
    else:
        created = ensure_home(root)
        setup_logging(root, debug=debug)

    try:
        migrate_0_0_1(root, fresh_install=created)
    except HomeError as exc:
        logger.error("migration_error %s", exc)
        raise ClientBootstrapError(str(exc)) from None

    if created:
        import aldwin

        mark_whats_new_seen(root, aldwin.__version__)

    ws = Workspace(
        workspace if workspace is not None else default_workspace_root(root),
        home=root,
    )
    ws_root = ws.root
    ws_display = ws.display_root

    try:
        config = load_config(root)
    except ConfigError as exc:
        logger.error("configuration_error %s", exc)
        raise ClientBootstrapError(str(exc)) from None

    effective_plain = (
        plain or bool(os.environ.get("NO_COLOR", "")) or config.theme == "plain"
    )

    logger.info(
        "aldwin.startup home=%s workspace=%s created=%s model=%s plain=%s",
        root,
        ws_root,
        created,
        config.model,
        effective_plain,
    )
    return BootstrapResult(
        home=root,
        display_home=format_home_path(root),
        created=created,
        model=config.model,
        plain=effective_plain,
        workspace=ws_root,
        display_workspace=ws_display,
        theme=config.theme,
    )


class ClientTransport(Protocol):
    """Client-side transport handle used by `AldwinClient`."""

    async def send(self, request: Request) -> None:
        """Send a protocol request to the host."""
        ...

    def events(self) -> AsyncIterator[Event]:
        """Yield protocol events from the host until the transport closes."""
        ...

    async def new_conversation(self) -> None:
        """Ask the host to close the current session file and start a new one."""
        ...

    async def close(self) -> None:
        """Close the transport connection."""
        ...


class _InProcessClientEndpoint:
    """Client-side view of an `InProcessTransport`."""

    def __init__(self, transport: InProcessTransport) -> None:
        self._transport = transport

    async def send(self, request: Request) -> None:
        """Validate `request` through JSON and enqueue it for the host."""
        await self._transport.put_request(request)

    async def events(self) -> AsyncIterator[Event]:
        """Yield events emitted by the host until a `None` sentinel arrives."""
        while True:
            event = await self._transport.next_event()
            if event is None:
                return
            yield event

    async def new_conversation(self) -> None:
        """Invoke the host's new-conversation hook."""
        await self._transport.trigger_new_conversation()

    async def close(self) -> None:
        """Close the shared in-process transport."""
        await self._transport.close()


class InProcessTransport:
    """Pair of `asyncio.Queue`s connecting client and host inside one process."""

    def __init__(
        self,
        *,
        on_new_conversation: NewConversationHandler | None = None,
    ) -> None:
        self._inbound: asyncio.Queue[Request | None] = asyncio.Queue()
        self._outbound: asyncio.Queue[Event | None] = asyncio.Queue()
        self._on_new_conversation = on_new_conversation
        self._closed = False

    @property
    def closed(self) -> bool:
        """Return `True` if this transport has been closed."""
        return self._closed

    def set_new_conversation_handler(self, handler: NewConversationHandler) -> None:
        """Register the host callback invoked when a client starts a new chat."""
        self._on_new_conversation = handler

    def connect(self) -> ClientTransport:
        """Return a client-side handle connected to this transport."""
        return _InProcessClientEndpoint(self)

    async def put_request(self, request: Request) -> None:
        """Round-trip `request` through JSON wire validation and enqueue it."""
        if self._closed:
            raise RuntimeError("Transport is closed.")
        from aldwin.protocol.messages import parse_request

        validated = parse_request(request.model_dump_json())
        await self._inbound.put(validated)

    async def receive_request(self) -> Request | None:
        """Await the next request from the client, or `None` when closed."""
        return await self._inbound.get()

    async def send_event(self, event: Event) -> None:
        """Round-trip `event` through JSON wire validation and enqueue it."""
        if self._closed:
            return
        from aldwin.protocol.messages import parse_event

        validated = parse_event(event.model_dump_json())
        await self._outbound.put(validated)

    async def next_event(self) -> Event | None:
        """Await the next event for the client, or `None` when closed."""
        return await self._outbound.get()

    async def trigger_new_conversation(self) -> None:
        """Run the registered `on_new_conversation` callback if present."""
        if self._on_new_conversation is not None:
            await self._on_new_conversation()

    async def close(self) -> None:
        """Signal both queues to stop waiting and mark the transport closed."""
        if self._closed:
            return
        self._closed = True
        await self._inbound.put(None)
        await self._outbound.put(None)


@asynccontextmanager
async def open_in_process_client(
    *,
    initial_model: str | None = None,
    home: Path | None = None,
    workspace: Path | str | None = None,
) -> AsyncIterator[AldwinClient]:
    """Start an in-process `AldwinHost` and yield a connected `AldwinClient`."""
    from aldwin.client.client import AldwinClient
    from aldwin.host.runner import AldwinHost
    from aldwin.host.sessions import SessionStore

    root = (home if home is not None else home_dir()).expanduser().resolve()
    transport = InProcessTransport()
    store = SessionStore(sessions_dir=root / "sessions")
    host = AldwinHost(
        transport=transport,
        session_store=store,
        initial_model=initial_model,
        home=root,
        workspace=workspace,
    )
    await host.start()
    client = AldwinClient(
        transport.connect(),
        model_getter=lambda: host.model,
        session_id_getter=lambda: host.stored_session.meta.id,
        updated_at_getter=lambda: host.stored_session.meta.updated_at,
        workspace_getter=host.workspace_summary,
        workspace_setter=host.set_workspace,
        home=root,
    )
    try:
        await client.connect()
        yield client
    finally:
        await client.close()
        await host.stop_host()


__all__ = [
    "ConnectorCommandError",
    "connectors_add_command",
    "connectors_remove_command",
    "connectors_set_enabled",
    "connectors_summary",
    "BootstrapResult",
    "ClientBootstrapError",
    "ClientTransport",
    "InProcessTransport",
    "InvalidSkillSummary",
    "KeyCheckResult",
    "MemorySummary",
    "NewConversationHandler",
    "NoteEntry",
    "OnboardingStatus",
    "PersonaSummary",
    "PlatformNotSupportedError",
    "SkillSummary",
    "SkillsReport",
    "ToolInfo",
    "WorkspaceError",
    "WorkspaceNotAllowedError",
    "WorkspaceNotFoundError",
    "WorkspaceSummary",
    "bootstrap_host",
    "check_openai_key",
    "verify_active_openai_key",
    "get_builtin_tools",
    "get_memory_summary",
    "get_onboarding_status",
    "get_persona_summary",
    "get_persona_text",
    "get_skills_report",
    "list_available_models",
    "mark_whats_new_seen",
    "mask_secret",
    "open_in_process_client",
    "pop_profile_truncation_warning",
    "purge_home_data",
    "resolve_workspace_summary",
    "sanitize_display_text",
    "save_setup",
    "update_available_nudge",
    "whats_new_due",
]


class ConnectorCommandError(Exception):
    """A connector shell command failed with a message to show the user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def connectors_summary(home: Path | None = None) -> list[dict[str, Any]]:
    """Every saved connector as plain data, for `aldwin connectors list`."""
    from aldwin.host.connectors import ConnectorAdmin

    admin = ConnectorAdmin(_resolve_home(home))
    load = admin.load()
    if load.error is not None:
        raise ConnectorCommandError(load.error)
    return [_connector_row(config) for config in load.entries.values()]


async def connectors_add_command(
    name: str,
    url: str,
    *,
    token_env: str | None = None,
    client_id: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """Probe a server, then save it. Nothing is saved when the probe fails.

    With `client_id` the connector signs in through the browser, which needs a
    person, so it is saved unchecked; `connectors_login_command` signs it in.
    """
    from aldwin.core.connectors.config import (
        ConnectorConfig,
        OAuthSettings,
        check_name,
        check_url,
    )
    from aldwin.core.connectors.errors import ConnectorError
    from aldwin.core.connectors.state import tool_infos
    from aldwin.host.connectors import ConnectorAdmin

    admin = ConnectorAdmin(_resolve_home(home))
    try:
        check_url(url)
        check_name(name, admin.load().entries)
        if client_id:
            config = ConnectorConfig(
                name=name,
                url=url,
                auth="oauth",
                oauth=OAuthSettings(client_id=client_id),
            )
            admin.save_new(config, None)
            return _connector_row(config)
        config = ConnectorConfig(
            name=name,
            url=url,
            auth="bearer" if token_env else "none",
            token_env=token_env,
        )
        specs = await admin.probe(config)
        admin.save_new(config, None)
    except ConnectorError as exc:
        raise ConnectorCommandError(exc.message) from None
    infos = tool_infos(config, specs)
    row = _connector_row(config)
    row["read"] = sum(1 for t in infos if t.effective == "auto")
    row["write"] = sum(1 for t in infos if t.effective == "ask")
    return row


async def connectors_login_command(
    name: str,
    *,
    paste: bool,
    show: Callable[[str], None],
    read_line: Callable[[str], Awaitable[str]],
    open_browser: Callable[[str], bool],
    home: Path | None = None,
) -> dict[str, Any]:
    """Sign a connector in through the browser and save the sign-in.

    A running Aldwin picks the new sign-in up on its next turn.
    """
    from aldwin.core.connectors.connection import McpConnection
    from aldwin.core.connectors.errors import ConnectorError
    from aldwin.core.connectors.oauth_messages import SIGN_IN_TIMEOUT_S
    from aldwin.core.connectors.sign_in import SignInWait, sign_in
    from aldwin.host.connectors import ConnectorAdmin

    admin = ConnectorAdmin(_resolve_home(home))
    load = admin.load()
    if load.error is not None:
        raise ConnectorCommandError(load.error)
    found = load.entries.get(name)
    if found is None:
        raise ConnectorCommandError(f"No connector named {name}.")
    config = found
    title = config.display_title
    if config.auth != "oauth":
        raise ConnectorCommandError(f"{title} doesn't use browser sign-in.")

    wait = SignInWait(name)
    readers: list[asyncio.Task[None]] = []

    async def read_pastes() -> None:
        while True:
            raw = (await read_line("Paste the address: ")).strip()
            try:
                wait.paste(raw)
                return
            except ConnectorError as exc:
                show(exc.message)

    async def on_auth_url(url: str) -> None:
        minutes = int(SIGN_IN_TIMEOUT_S // 60)
        show(f"Sign in to {title}. This link works for {minutes} minutes:")
        show(sanitize_display_text(url))
        if not paste and open_browser(url):
            show("Waiting for you to finish in the browser…")
            return
        show(
            "Open the address above in a browser and sign in, then paste the "
            "address the browser ends on."
        )
        readers.append(asyncio.create_task(read_pastes()))

    async def ignore(*_args: Any) -> None:
        return None

    try:
        signed = await sign_in(
            config,
            admin.store,
            cimd_url=load.oauth_client_metadata_url,
            no_browser=paste,
            wait=wait,
            on_auth_url=on_auth_url,
            connect=lambda provider: McpConnection(
                config, auth=provider, on_tools_changed=ignore, on_lost=ignore
            ),
        )
    except ConnectorError as exc:
        raise ConnectorCommandError(exc.message) from None
    finally:
        for task in readers:
            task.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
    if signed is None:
        return {**_connector_row(admin.update(name, auth="none")), "tools_count": None}
    await signed.connection.close()
    return {**_connector_row(config), "tools_count": len(signed.specs)}


def connectors_logout_command(name: str, *, home: Path | None = None) -> None:
    """Delete a connector's sign-in. Nothing is revoked at the server."""
    from aldwin.host.connectors import ConnectorAdmin

    admin = ConnectorAdmin(_resolve_home(home))
    config = admin.load().entries.get(name)
    if config is None:
        raise ConnectorCommandError(f"No connector named {name}.")
    if config.auth != "oauth":
        raise ConnectorCommandError(
            f"{config.display_title} doesn't use browser sign-in."
        )
    admin.sign_out(name)


def connectors_remove_command(name: str, *, home: Path | None = None) -> None:
    """Delete a connector and its saved token."""
    from aldwin.core.connectors.errors import ConnectorError
    from aldwin.host.connectors import ConnectorAdmin

    try:
        ConnectorAdmin(_resolve_home(home)).remove(name)
    except ConnectorError as exc:
        raise ConnectorCommandError(exc.message) from None


def connectors_set_enabled(
    name: str, enabled: bool, *, home: Path | None = None
) -> dict[str, Any]:
    """Turn a connector on or off; running Aldwins pick it up on their next turn."""
    from aldwin.core.connectors.errors import ConnectorError
    from aldwin.host.connectors import ConnectorAdmin

    try:
        config = ConnectorAdmin(_resolve_home(home)).update(name, enabled=enabled)
    except ConnectorError as exc:
        raise ConnectorCommandError(exc.message) from None
    return _connector_row(config)


def _connector_row(config: Any) -> dict[str, Any]:
    return {
        "name": config.name,
        "title": config.display_title,
        "url": config.url,
        "enabled": config.enabled,
        "auth": config.auth,
        "token_env": config.token_env,
        "trust_read_only_hints": config.trust_read_only_hints,
        "tools": dict(config.tools),
    }


def _resolve_home(home: Path | None) -> Path:
    return (home if home is not None else home_dir()).expanduser().resolve()
