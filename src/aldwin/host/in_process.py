"""In-process host bootstrap, setup/inspect bridge, and queue transport for Aldwin."""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import shlex
import shutil
import signal
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
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
from aldwin.core.providers.base import AccessStatus, ModelInfo
from aldwin.core.providers.discovery import LocalProviderStatus
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
    from aldwin.core.providers import ModelInfo
    from aldwin.host.runner import AldwinHost
    from aldwin.host.transport import HostTransport
    from aldwin.protocol import ApprovalEvent, Event, Request

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
    provider: str = "openai"
    has_provider_key: bool = True
    is_local: bool = False


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
    """Inspect whether usable keys and models exist and return masked metadata."""
    from aldwin.core.providers.registry import get_provider, parse_model_ref

    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    openai_res = resolve_key("openai", root)
    tavily_res = resolve_key("tavily", root)

    ref = parse_model_ref(config.model)
    entry = get_provider(ref.provider, config)
    is_local = entry.local if entry is not None else False
    if entry is not None and entry.secret:
        prov_res = resolve_key(entry.name, root)
        has_provider_key = bool(prov_res.key)
    else:
        has_provider_key = True

    return OnboardingStatus(
        has_openai_key=bool(openai_res.key),
        openai_source=openai_res.source,
        masked_openai_key=mask_secret(openai_res.key),
        has_tavily_key=bool(tavily_res.key),
        tavily_source=tavily_res.source,
        masked_tavily_key=mask_secret(tavily_res.key),
        model=config.model,
        provider=ref.provider,
        has_provider_key=has_provider_key,
        is_local=is_local,
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


async def save_provider_key(
    provider: str,
    key: str,
    *,
    home: Path | None = None,
    base_url: str | None = None,
) -> AccessStatus:
    """Validate a candidate provider key and persist to secrets.yaml only on success."""
    from aldwin.core.providers.keys import save_provider_key as _core_save_key

    return await _core_save_key(provider, key, home=home, base_url=base_url)


async def check_search_key(provider: str, key: str) -> tuple[bool, str]:
    """Check a search key with its provider; return ``(ok, reason)`` for a door."""
    from aldwin.core.tools.web_search.connect import check_search_key as _check
    from aldwin.core.tools.web_search.hosted import KeyCheck
    from aldwin.core.tools.web_search.shared import hosted_provider_classes
    from aldwin.host.runner import key_check_error

    result = await _check(provider, key)
    if result.outcome is KeyCheck.OK:
        return True, ""
    title = hosted_provider_classes()[provider].name
    return False, key_check_error(title, provider, result)[1]


async def verify_active_provider_key(
    provider: str,
    home: Path | None = None,
) -> AccessStatus:
    """Validate the resolved key for `provider` without exposing it to doors."""
    from aldwin.core.providers.factory import get_backend

    root = (home if home is not None else home_dir()).expanduser().resolve()
    resolved = resolve_key(provider, root)
    key = (resolved.key or "").strip()
    if not key:
        return AccessStatus.INVALID_KEY

    backend = get_backend(provider, home=root)
    return await backend.check_access()


async def detect_local_providers(
    *,
    timeout: float = 1.0,
    force_refresh: bool = False,
    home: Path | None = None,
) -> list[LocalProviderStatus]:
    """Inspect loopback ports for Ollama and LM Studio runtimes."""
    from aldwin.core.providers.discovery import detect_local_providers as _core_detect

    return await _core_detect(timeout=timeout, force_refresh=force_refresh)


async def list_models_for(
    provider: str,
    home: Path | None = None,
) -> list[ModelInfo]:
    """List available models for `provider`, returning [] on error."""
    from aldwin.core.providers import get_backend
    from aldwin.core.settings import load_config, load_secrets

    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    secrets = load_secrets(root)

    try:
        backend = get_backend(provider, config=config, secrets=secrets, home=root)
        return await backend.list_models()
    except Exception as exc:
        logger.warning("Failed to list models for provider %r: %s", provider, exc)
        return []


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
        from aldwin.core.settings import load_secrets

        existing = load_secrets(root)
        updated = existing.model_dump()
        if openai_clean:
            updated["openai_api_key"] = SecretStr(openai_clean)
        if tavily_clean:
            updated["tavily_api_key"] = SecretStr(tavily_clean)
        secrets = SecretsConfig(**updated)
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


@dataclass(frozen=True, slots=True)
class SearchSwitchResult:
    """Outcome of inspecting or switching the search backend (door-facing)."""

    ok: bool
    message: str
    provider: str | None = None


def describe_active_search(home: Path | None = None) -> str:
    """Status text for bare `/search` (re-detects local SearXNG)."""
    from aldwin.core.tools.web_search import describe_active_search as _describe

    return _describe(_resolve_home(home))


def switch_search_provider(
    raw_name: str, *, home: Path | None = None
) -> SearchSwitchResult:
    """Validate and persist ``search.provider`` for `/search <name>`."""
    from aldwin.core.tools.web_search import switch_search_provider as _switch

    result = _switch(raw_name, home=_resolve_home(home))
    return SearchSwitchResult(
        ok=result.ok, message=result.message, provider=result.provider
    )


def switchable_search_providers() -> tuple[str, ...]:
    """Provider names accepted by `/search` autocomplete."""
    from aldwin.core.tools.web_search import switchable_search_providers as _names

    return _names()


def active_search_provider_name(home: Path | None = None) -> str | None:
    """Display name of the search backend that would run, or None."""
    from aldwin.core.tools.web_search import (
        active_search_provider_name as _active_name,
    )

    return _active_name(_resolve_home(home))


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

    @property
    def attached(self) -> bool:
        """Return `True` while open: the in-process peer cannot detach and return."""
        return not self._closed

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


async def start_host(
    transport: HostTransport,
    *,
    home: Path | None = None,
    workspace: Path | str | None = None,
) -> AldwinHost:
    """Start an `AldwinHost` serving `transport`; stop it with `stop_host()`.

    For doors that own their transport, such as the browser's WebSocket.
    """
    from aldwin.host.runner import AldwinHost
    from aldwin.host.sessions import SessionStore

    root = (home if home is not None else home_dir()).expanduser().resolve()
    host = AldwinHost(
        transport=transport,
        session_store=SessionStore(sessions_dir=root / "sessions"),
        home=root,
        workspace=workspace,
    )
    await host.start()
    _register_exit_backstop()
    return host


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
    _register_exit_backstop()
    terminated = _CancelOnSigterm()
    try:
        await client.connect()
        yield client
    finally:
        try:
            await client.close()
            await host.stop_host()
        finally:
            terminated.uninstall()
        if terminated.fired:
            # Python's default SIGTERM action would skip this cleanup entirely.
            raise SystemExit(128 + signal.SIGTERM)


class _CancelOnSigterm:
    """Turn SIGTERM into a cancel of the current task, so the `finally` that
    stops local servers runs. Only possible on the main thread's loop.
    """

    def __init__(self) -> None:
        self.fired = False
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self._installed = False
        try:
            self._loop.add_signal_handler(signal.SIGTERM, self._on_signal)
            self._installed = True
        except (NotImplementedError, RuntimeError, ValueError):
            pass  # not the main thread, or no signal support on this platform

    def _on_signal(self) -> None:
        if self.fired:
            return  # a second SIGTERM must not cancel the cleanup already running
        self.fired = True
        if self._task is not None:
            self._task.cancel()

    def uninstall(self) -> None:
        if self._installed:
            self._loop.remove_signal_handler(signal.SIGTERM)
            self._installed = False


_exit_backstop_registered = False


def _register_exit_backstop() -> None:
    """At interpreter exit, kill any local server group still running."""
    global _exit_backstop_registered
    if not _exit_backstop_registered:
        atexit.register(_kill_local_servers)
        _exit_backstop_registered = True


def _kill_local_servers() -> None:
    # Only loaded once a local server has started; nothing to do otherwise.
    stdio = sys.modules.get("aldwin.core.connectors.stdio")
    if stdio is not None:
        stdio.kill_live_groups()


__all__ = [
    "AccessStatus",
    "ConnectorCommandError",
    "ModelInfo",
    "ShellApprovalHandler",
    "connector_needs_approval",
    "connectors_add_command",
    "connectors_add_local_command",
    "connectors_enable_command",
    "connectors_find_configs",
    "connectors_import_command",
    "connectors_remove_command",
    "connectors_set_enabled",
    "connectors_summary",
    "BootstrapResult",
    "ClientBootstrapError",
    "ClientTransport",
    "InProcessTransport",
    "InvalidSkillSummary",
    "KeyCheckResult",
    "LocalProviderStatus",
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
    "detect_local_providers",
    "verify_active_openai_key",
    "get_builtin_tools",
    "get_memory_summary",
    "get_onboarding_status",
    "get_persona_summary",
    "get_persona_text",
    "get_skills_report",
    "list_models_for",
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


# Answers a host approval in the shell: the answer and an optional reason.
ShellApprovalHandler = Callable[["ApprovalEvent"], Awaitable[tuple[str, str | None]]]


def _shell_approve(on_approval: ShellApprovalHandler) -> Any:
    """Turn the CLI's answer into the host's: the host builds the question and
    logs the answer; the CLI only shows it and replies.
    """
    import uuid

    from aldwin.core.policy import ApprovalAnswer, ApprovalRequest
    from aldwin.protocol import ApprovalEvent

    async def approve(request: ApprovalRequest) -> ApprovalAnswer:
        event = ApprovalEvent(
            re="shell",
            approval_id=uuid.uuid4().hex,
            tool=request.tool,
            tier=request.tier,
            summary=request.summary,
            detail=request.detail or "",
            grantable=request.grantable,
            digest=request.digest,
            risky=list(request.risky),
            connector=request.connector,
        )
        answer, reason = await on_approval(event)
        if answer == "yes":
            return ApprovalAnswer("yes", "user", reason)
        return ApprovalAnswer("no", "user", reason)

    return approve


async def connectors_add_local_command(
    name: str,
    command: str,
    *,
    env_allow: Sequence[str] = (),
    cwd: str | None = None,
    on_approval: ShellApprovalHandler,
    home: Path | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Approve, start and save a local connector. Nothing is saved on `no` or on
    any failure; the program is stopped again before this returns.
    """
    from aldwin.core.approval_log import ApprovalLogger
    from aldwin.core.connectors.config import ConnectorConfig
    from aldwin.core.connectors.errors import ConnectorError
    from aldwin.core.connectors.local import ADD_TOOL, parse_command_line
    from aldwin.core.connectors.state import tool_infos
    from aldwin.host.connectors import ConnectorAdmin

    root = _resolve_home(home)
    admin = ConnectorAdmin(root)
    try:
        program, args = parse_command_line(command)
        config = ConnectorConfig(
            name=name,
            command=program,
            args=args,
            env_allow=list(env_allow),
            cwd=str(Path(cwd).expanduser().resolve()) if cwd else "workspace",
        )
        admin.check_new(config)
        workspace_root = workspace or default_workspace_root(root)
        approved = await admin.approve_local(
            config,
            tool=ADD_TOOL,
            workspace_root=workspace_root,
            approve=_shell_approve(on_approval),
            approval_log=ApprovalLogger(home=root),
        )
        specs = await _probe_local(admin, approved, workspace_root)
        admin.save_new(approved, None)
    except ConnectorError as exc:
        raise ConnectorCommandError(exc.message) from None
    infos = tool_infos(approved, specs)
    row = _connector_row(approved)
    row["read"] = sum(1 for t in infos if t.effective == "auto")
    row["write"] = sum(1 for t in infos if t.effective == "ask")
    return row


async def _probe_local(admin: Any, config: Any, workspace_root: Path) -> list[Any]:
    from aldwin.core.connectors.connection import probe_connector
    from aldwin.core.connectors.stderr_log import ServerLog
    from aldwin.core.connectors.stdio import StdioTransport

    spec = admin.launch_spec(config, workspace_root=workspace_root)
    specs: list[Any] = await probe_connector(
        config,
        None,
        transport_factory=lambda _http: StdioTransport(
            spec, log=ServerLog(admin.home, config.name)
        ),
    )
    return specs


def connector_needs_approval(name: str, *, home: Path | None = None) -> bool:
    """True for a local connector that would ask before it is turned on."""
    from aldwin.core.connectors.local import is_approved
    from aldwin.host.connectors import ConnectorAdmin

    config = ConnectorAdmin(_resolve_home(home)).load().entries.get(name)
    return config is not None and config.kind == "local" and not is_approved(config)


async def connectors_enable_command(
    name: str,
    *,
    on_approval: ShellApprovalHandler,
    home: Path | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Turn a connector on. A local one not yet approved asks first and stays off
    on `no`; running Aldwins pick the change up on their next turn.
    """
    from aldwin.core.approval_log import ApprovalLogger
    from aldwin.core.connectors.errors import ConnectorError
    from aldwin.core.connectors.local import ENABLE_TOOL
    from aldwin.host.connectors import ConnectorAdmin

    root = _resolve_home(home)
    admin = ConnectorAdmin(root)
    try:
        if not connector_needs_approval(name, home=root):
            return _connector_row(admin.update(name, enabled=True))
        config = admin.load().entries[name]
        approved = await admin.approve_local(
            config,
            tool=ENABLE_TOOL,
            workspace_root=workspace or default_workspace_root(root),
            approve=_shell_approve(on_approval),
            approval_log=ApprovalLogger(home=root),
        )
        updated = admin.update(
            name, enabled=True, approved_digest=approved.approved_digest, note=None
        )
    except ConnectorError as exc:
        raise ConnectorCommandError(exc.message) from None
    return _connector_row(updated)


def connectors_find_configs(
    *, home: Path | None = None, workspace: Path | None = None
) -> dict[str, Any]:
    """MCP config files other apps keep in the usual places, plus the files
    named in `connectors.import_paths`. Reads only; problems become warnings.
    """
    from aldwin.core.connectors.importer import find_config_files
    from aldwin.core.settings import ConfigError, load_config

    root = _resolve_home(home)
    setup_logging(root)
    try:
        extra = load_config(root).connectors.import_paths
    except ConfigError as exc:
        raise ConnectorCommandError(str(exc)) from None
    found, warnings = find_config_files(
        user_home=Path.home(),
        workspace=workspace or default_workspace_root(root),
        extra_paths=extra,
    )
    return {
        "found": [
            {
                "app": f.app,
                "scope": f.scope,
                "path": str(f.path),
                "servers": f.servers,
                "error": f.error,
            }
            for f in found
        ],
        "warnings": warnings,
    }


def connectors_import_command(
    path: Path,
    *,
    rename_conflicts: bool = False,
    home: Path | None = None,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Import the servers a config file lists. Never starts or asks anything;
    running Aldwins pick the new entries up on their next turn.
    """
    from aldwin.core.connectors.errors import ConnectorError
    from aldwin.host.connectors import ConnectorAdmin

    root = _resolve_home(home)
    setup_logging(root)
    admin = ConnectorAdmin(root)
    try:
        result = admin.import_file(
            path,
            rename_conflicts=rename_conflicts,
            workspace=workspace or default_workspace_root(root),
        )
    except ConnectorError as exc:
        raise ConnectorCommandError(exc.message) from None
    return {
        "app": result.app,
        "rows": [
            {"name": r.name, "kind": r.kind, "status": r.status, "detail": r.detail}
            for r in result.rows
        ],
        "imported": result.imported,
        "values": result.values,
        "warnings": list(result.warnings),
    }


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
        "command": (
            shlex.join([config.command, *config.args]) if config.command else None
        ),
        "enabled": config.enabled,
        "auth": config.auth,
        "token_env": config.token_env,
        "trust_read_only_hints": config.trust_read_only_hints,
        "tools": dict(config.tools),
    }


def _resolve_home(home: Path | None) -> Path:
    return (home if home is not None else home_dir()).expanduser().resolve()
