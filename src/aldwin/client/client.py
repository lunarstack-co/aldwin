"""Client API that Python doors use to communicate with the Aldwin host."""

from __future__ import annotations

import asyncio
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Callable,
    Sequence,
)
from pathlib import Path
from typing import Final, Literal, TypeVar

from aldwin.host.in_process import (
    BootstrapResult,
    ClientBootstrapError,
    ClientTransport,
    ConnectorCommandError,
    InvalidSkillSummary,
    KeyCheckResult,
    MemorySummary,
    NoteEntry,
    OnboardingStatus,
    PersonaSummary,
    PlatformNotSupportedError,
    SearchSwitchResult,
    SkillsReport,
    SkillSummary,
    ToolInfo,
    WorkspaceError,
    WorkspaceNotAllowedError,
    WorkspaceNotFoundError,
    WorkspaceSummary,
    bootstrap_host,
    check_openai_key,
    connector_needs_approval,
    connectors_add_command,
    connectors_add_local_command,
    connectors_enable_command,
    connectors_find_configs,
    connectors_import_command,
    connectors_remove_command,
    connectors_set_enabled,
    connectors_summary,
    get_builtin_tools,
    get_memory_summary,
    get_onboarding_status,
    get_persona_summary,
    get_persona_text,
    get_skills_report,
    mark_whats_new_seen,
    mask_secret,
    open_in_process_client,
    pop_profile_truncation_warning,
    purge_home_data,
    resolve_workspace_summary,
    save_setup,
    update_available_nudge,
    verify_active_openai_key,
    whats_new_due,
)
from aldwin.host.in_process import (
    active_search_provider_name as get_active_search_provider_name,
)
from aldwin.host.in_process import (
    describe_active_search as get_describe_active_search,
)
from aldwin.host.in_process import (
    switch_search_provider as apply_switch_search_provider,
)
from aldwin.host.in_process import (
    switchable_search_providers as list_switchable_search_providers,
)
from aldwin.protocol import (
    HOST_EVENT_RE,
    MIN_SUPPORTED_VERSION,
    PROTOCOL_VERSION,
    ApprovalAnswer,
    ApprovalEvent,
    ApproveRequest,
    ConnectorAuth,
    ConnectorProbeEvent,
    ConnectorsAddRequest,
    ConnectorsAuthPasteRequest,
    ConnectorsAuthTokenRequest,
    ConnectorsDisableRequest,
    ConnectorsEnableRequest,
    ConnectorsEvent,
    ConnectorsImportEvent,
    ConnectorsImportRequest,
    ConnectorsListRequest,
    ConnectorsLoginRequest,
    ConnectorsLogoutRequest,
    ConnectorsReconnectRequest,
    ConnectorsRemoveRequest,
    ConnectorStatusEvent,
    ConnectorsToolPolicyRequest,
    ConnectorsTrustHintsRequest,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    Event,
    HelloRequest,
    HistoryEvent,
    HistoryRequest,
    ModelsEvent,
    ModelsListRequest,
    NoticeEvent,
    ProviderDisconnectRequest,
    ProviderKeyRequest,
    ProviderServerRequest,
    Request,
    SearchConnectRequest,
    SearchDisconnectRequest,
    SearchListRequest,
    SearchProvidersEvent,
    SearchUseRequest,
    SessionsEvent,
    SessionsListRequest,
    SessionsNewRequest,
    SessionsOpenRequest,
    SetModelRequest,
    SkillBodyEvent,
    SkillsEvent,
    SkillsListRequest,
    SkillsShowRequest,
    StopRequest,
    TodoGetRequest,
    TodoUpdatedEvent,
    TurnRequest,
)

# Answers a host `approval` event: the answer and an optional reason.
ApprovalHandler = Callable[
    [ApprovalEvent], Awaitable[tuple[ApprovalAnswer, str | None]]
]
_E = TypeVar("_E", bound=Event)


def bootstrap(
    *,
    plain: bool = False,
    debug: bool = False,
    workspace: Path | str | None = None,
) -> BootstrapResult:
    """Initialise the Aldwin host and return startup metadata for the door."""
    return bootstrap_host(plain=plain, debug=debug, workspace=workspace)


class ProtocolError(Exception):
    """Raised when the host rejects a handshake or closes before a terminal event."""

    def __init__(self, message: str, *, code: ErrorCode | None = None) -> None:
        super().__init__(message)
        self.code: Final[ErrorCode | None] = code


class AldwinClient:
    """Async client that every Python door uses to talk to the Aldwin host."""

    def __init__(
        self,
        transport: ClientTransport,
        *,
        model_getter: Callable[[], str] | None = None,
        session_id_getter: Callable[[], str] | None = None,
        updated_at_getter: Callable[[], str | None] | None = None,
        workspace_getter: Callable[[], WorkspaceSummary] | None = None,
        workspace_setter: Callable[[Path | str], WorkspaceSummary] | None = None,
        home: Path | None = None,
    ) -> None:
        self._transport = transport
        self._queues: dict[str, asyncio.Queue[Event | None]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._connected_event: DoneEvent | None = None
        self._closed = False
        self._model_getter = model_getter
        self._session_id_getter = session_id_getter
        self._updated_at_getter = updated_at_getter
        self._workspace_getter = workspace_getter
        self._workspace_setter = workspace_setter
        self._home = home
        self._fallback_model = "gpt-4o"
        self._fallback_session_id = "new"
        self._fallback_workspace = WorkspaceSummary(
            root=Path.cwd().resolve(),
            display_root=str(Path.cwd().resolve()),
            granted_folders=(),
        )

    @property
    def model(self) -> str:
        """Return the currently active model name."""
        if self._model_getter is not None:
            return self._model_getter()
        return self._fallback_model

    @property
    def session_id(self) -> str:
        """Return the active conversation session ID."""
        if self._session_id_getter is not None:
            return self._session_id_getter()
        return self._fallback_session_id

    @property
    def updated_at(self) -> str | None:
        """Return the ISO-8601 timestamp when the active session was last updated."""
        if self._updated_at_getter is not None:
            return self._updated_at_getter()
        return None

    def _ensure_reader(self) -> None:
        if self._reader_task is None or self._reader_task.done():
            self._reader_task = asyncio.create_task(self._read_events_loop())

    async def _read_events_loop(self) -> None:
        try:
            async for event in self._transport.events():
                if isinstance(event, (DoneEvent, ErrorEvent)):
                    queue = self._queues.pop(event.re, None)
                else:
                    queue = self._queues.get(event.re)
                if queue is not None:
                    await queue.put(event)
        finally:
            for queue in list(self._queues.values()):
                await queue.put(None)

    async def _request_terminal(self, request: Request) -> DoneEvent | ErrorEvent:
        self._ensure_reader()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError(
                        f"Transport closed before request {request.id!r} completed."
                    )
                if isinstance(event, (DoneEvent, ErrorEvent)):
                    return event
        finally:
            self._queues.pop(request.id, None)

    async def _request_with_notices(
        self, request: Request
    ) -> tuple[DoneEvent | ErrorEvent, list[str]]:
        """Like `_request_terminal`, also returning any notices sent on the way."""
        self._ensure_reader()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        notices: list[str] = []
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError(
                        f"Transport closed before request {request.id!r} completed."
                    )
                if isinstance(event, NoticeEvent):
                    notices.append(event.message)
                elif isinstance(event, (DoneEvent, ErrorEvent)):
                    return event, notices
        finally:
            self._queues.pop(request.id, None)

    async def connect(
        self,
        *,
        v_min: int = MIN_SUPPORTED_VERSION,
        v_max: int = PROTOCOL_VERSION,
    ) -> DoneEvent:
        """Perform the initial `hello` handshake with the host."""
        if (
            self._connected_event is not None
            and v_min == MIN_SUPPORTED_VERSION
            and v_max == PROTOCOL_VERSION
        ):
            return self._connected_event
        terminal = await self._request_terminal(HelloRequest(v_min=v_min, v_max=v_max))
        if isinstance(terminal, ErrorEvent):
            raise ProtocolError(terminal.message, code=terminal.code)
        self._connected_event = terminal
        return terminal

    async def turn(
        self,
        text: str,
        *,
        skills: Sequence[str] = (),
    ) -> AsyncGenerator[Event, None]:
        """Send a `turn` request and yield events until `done` or `error`."""
        async for event in self._stream(TurnRequest(text=text, skills=list(skills))):
            yield event

    async def _stream(self, request: Request) -> AsyncGenerator[Event, None]:
        """Send `request` and yield its events until `done` or `error`."""
        self._ensure_reader()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
                if isinstance(event, (DoneEvent, ErrorEvent)):
                    return
        finally:
            self._queues.pop(request.id, None)

    async def stop(self) -> DoneEvent | ErrorEvent:
        """Stop the currently running turn."""
        return await self._request_terminal(StopRequest())

    async def approve(
        self,
        approval_id: str,
        answer: ApprovalAnswer,
        *,
        digest: str | None = None,
        reason: str | None = None,
    ) -> DoneEvent | ErrorEvent:
        """Send an approval answer (`yes`, `yes_session`, or `no`)."""
        return await self._request_terminal(
            ApproveRequest(
                approval_id=approval_id,
                answer=answer,
                digest=digest,
                reason=reason,
            )
        )

    async def history(self, limit: int = 20) -> HistoryEvent:
        """Fetch recent conversation messages and `interrupted` flag."""
        self._ensure_reader()
        request = HistoryRequest(limit=limit)
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        history_event: HistoryEvent | None = None
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError("Transport closed before `history` completed.")
                if isinstance(event, HistoryEvent):
                    history_event = event
                elif isinstance(event, ErrorEvent):
                    raise ProtocolError(event.message, code=event.code)
                elif isinstance(event, DoneEvent):
                    if history_event is None:
                        return HistoryEvent(re=request.id)
                    return history_event
        finally:
            self._queues.pop(request.id, None)

    async def sessions_list(self) -> SessionsEvent:
        """List saved conversations with turns, newest first, and the active one."""
        return await self._one(SessionsListRequest(), SessionsEvent)

    async def sessions_open(self, session_id: str) -> SessionsEvent:
        """Switch to a saved conversation; fetch `history` afterwards to show it.

        Raises `ProtocolError` with code `busy` mid-turn or `not_found`.
        """
        return await self._one(
            SessionsOpenRequest(session_id=session_id), SessionsEvent
        )

    async def sessions_new(self) -> SessionsEvent:
        """Start a conversation, reusing the latest one while it is still empty.

        Raises `ProtocolError` with code `busy` mid-turn.
        """
        return await self._one(SessionsNewRequest(), SessionsEvent)

    async def todo_get(self) -> TodoUpdatedEvent:
        """Fetch the current session todo list from the host."""
        self._ensure_reader()
        request = TodoGetRequest()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        todo_event: TodoUpdatedEvent | None = None
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError("Transport closed before `todo.get` completed.")
                if isinstance(event, TodoUpdatedEvent):
                    todo_event = event
                elif isinstance(event, ErrorEvent):
                    raise ProtocolError(event.message, code=event.code)
                elif isinstance(event, DoneEvent):
                    if todo_event is None:
                        return TodoUpdatedEvent(re=request.id)
                    return todo_event
        finally:
            self._queues.pop(request.id, None)

    async def set_model(self, name: str) -> DoneEvent | ErrorEvent:
        """Switch the active model for subsequent turns."""
        result = await self._request_terminal(SetModelRequest(name=name))
        if isinstance(result, DoneEvent):
            cleaned = name.strip()
            self._fallback_model = cleaned if "/" in cleaned else f"openai/{cleaned}"
        return result

    async def new_conversation(self) -> None:
        """Close the current session file and start a new conversation."""
        await self._transport.new_conversation()

    async def list_models(
        self,
        *,
        provider: str | None = None,
        refresh: bool = False,
    ) -> ModelsEvent:
        """Fetch model inventory from host via `models.list` protocol request."""
        self._ensure_reader()
        request = ModelsListRequest(provider=provider, refresh=refresh)
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        models_event: ModelsEvent | None = None
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError(
                        "Transport closed before `models.list` completed."
                    )
                if isinstance(event, ModelsEvent):
                    models_event = event
                elif isinstance(event, ErrorEvent):
                    raise ProtocolError(event.message, code=event.code)
                elif isinstance(event, DoneEvent):
                    if models_event is None:
                        return ModelsEvent(
                            re=request.id, current=self.model, providers=[]
                        )
                    return models_event
        finally:
            self._queues.pop(request.id, None)

    async def save_provider_key(
        self,
        provider: str,
        key: str,
    ) -> DoneEvent | ErrorEvent:
        """Save and verify a provider API key over the protocol."""
        return await self._request_terminal(
            ProviderKeyRequest(provider=provider, key=key)
        )

    async def disconnect_provider(self, provider: str) -> DoneEvent | ErrorEvent:
        """Remove a cloud model provider's saved key."""
        return await self._request_terminal(
            ProviderDisconnectRequest(provider=provider)
        )

    async def search_list(self) -> SearchProvidersEvent:
        """Fetch every web search provider and how it is connected."""
        self._ensure_reader()
        request = SearchListRequest()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        result: SearchProvidersEvent | None = None
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError(
                        "Transport closed before `search.list` completed."
                    )
                if isinstance(event, SearchProvidersEvent):
                    result = event
                elif isinstance(event, ErrorEvent):
                    raise ProtocolError(event.message, code=event.code)
                elif isinstance(event, DoneEvent):
                    if result is None:
                        return SearchProvidersEvent(re=request.id, setting="auto")
                    return result
        finally:
            self._queues.pop(request.id, None)

    async def search_connect(
        self,
        provider: str,
        *,
        key: str | None = None,
        url: str | None = None,
    ) -> tuple[DoneEvent | ErrorEvent, list[str]]:
        """Check and save a search key or SearXNG address; return notices too."""
        return await self._request_with_notices(
            SearchConnectRequest(provider=provider, key=key, url=url)
        )

    async def search_use(self, provider: str) -> DoneEvent | ErrorEvent:
        """Use a connected search provider, or ``auto``."""
        return await self._request_terminal(SearchUseRequest(provider=provider))

    async def search_disconnect(self, provider: str) -> DoneEvent | ErrorEvent:
        """Forget a search provider's saved key or address."""
        return await self._request_terminal(SearchDisconnectRequest(provider=provider))

    async def add_provider_server(
        self,
        name: str,
        base_url: str,
        *,
        key: str | None = None,
        title: str | None = None,
        local: bool = False,
    ) -> DoneEvent | ErrorEvent:
        """Register a custom provider server over the protocol."""
        return await self._request_terminal(
            ProviderServerRequest(
                name=name,
                base_url=base_url,
                key=key,
                title=title,
                local=local,
            )
        )

    def persona_summary(self) -> PersonaSummary:
        """Return current persona text and path metadata for `/persona`."""
        return get_persona_summary(self._home)

    def describe_active_search(self) -> str:
        """Return status text for bare `/search`."""
        return get_describe_active_search(self._home)

    def switch_search_provider(self, raw_name: str) -> SearchSwitchResult:
        """Validate and persist ``search.provider`` for `/search <name>`."""
        return apply_switch_search_provider(raw_name, home=self._home)

    def switchable_search_providers(self) -> tuple[str, ...]:
        """Return provider names accepted by `/search` autocomplete."""
        return list_switchable_search_providers()

    def active_search_provider_name(self) -> str | None:
        """Return the display name of the active search backend, or None."""
        return get_active_search_provider_name(self._home)

    def memory_summary(self) -> MemorySummary:
        """Return profile and recent notes summary for `/memory`."""
        return get_memory_summary(self._home)

    def skills_report(self) -> SkillsReport:
        """Return valid and invalid skills report for `/skills`."""
        return get_skills_report(self._home)

    async def skills_list(self) -> SkillsEvent:
        """Fetch the active skill catalogue from the host."""
        self._ensure_reader()
        request = SkillsListRequest()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        skills_event: SkillsEvent | None = None
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError(
                        "Transport closed before `skills.list` completed."
                    )
                if isinstance(event, SkillsEvent):
                    skills_event = event
                elif isinstance(event, ErrorEvent):
                    raise ProtocolError(event.message, code=event.code)
                elif isinstance(event, DoneEvent):
                    if skills_event is None:
                        return SkillsEvent(re=request.id)
                    return skills_event
        finally:
            self._queues.pop(request.id, None)

    async def skills_show(self, name: str) -> SkillBodyEvent:
        """Fetch the effective body of one skill from the host."""
        self._ensure_reader()
        request = SkillsShowRequest(name=name)
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        body_event: SkillBodyEvent | None = None
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError(
                        "Transport closed before `skills.show` completed."
                    )
                if isinstance(event, SkillBodyEvent):
                    body_event = event
                elif isinstance(event, ErrorEvent):
                    raise ProtocolError(event.message, code=event.code)
                elif isinstance(event, DoneEvent):
                    if body_event is None:
                        raise ProtocolError(f"No skill named {name!r}.")
                    return body_event
        finally:
            self._queues.pop(request.id, None)

    async def connectors_import(
        self,
        paths: Sequence[str] = (),
        *,
        dry_run: bool = False,
        rename_conflicts: bool = False,
    ) -> ConnectorsImportEvent:
        """Import MCP servers from other agent apps' config files.

        `dry_run` only lists the files found. Otherwise `paths` are imported,
        or every readable file found when `paths` is empty. Nothing starts:
        imported local commands are saved disabled.
        """
        return await self._one(
            ConnectorsImportRequest(
                paths=list(paths), dry_run=dry_run, rename_conflicts=rename_conflicts
            ),
            ConnectorsImportEvent,
        )

    async def connectors_list(self) -> ConnectorsEvent:
        """Every connector, plus the `connectors.yaml` error if the file is bad."""
        return await self._one(ConnectorsListRequest(), ConnectorsEvent)

    async def connectors_add(
        self,
        *,
        url: str | None = None,
        command: str | None = None,
        env_allow: Sequence[str] = (),
        cwd: str | None = None,
        name: str | None = None,
        title: str | None = None,
        auth: ConnectorAuth = "none",
        token: str | None = None,
        token_env: str | None = None,
        dry_run: bool = False,
        on_approval: ApprovalHandler | None = None,
    ) -> ConnectorProbeEvent | ConnectorStatusEvent:
        """Probe (`dry_run`) or add a connector: a remote `url` or a local
        `command`. A local add asks first through `on_approval`; without one
        the answer is no.

        Raises `ProtocolError` with `code="auth_required"` when the server needs
        a token, or `code="connector"` with the message to show.
        """
        request = ConnectorsAddRequest(
            url=url,
            command=command,
            env_allow=list(env_allow),
            cwd=cwd,
            name=name,
            title=title,
            auth=auth,
            token=token,
            token_env=token_env,
            dry_run=dry_run,
        )
        if dry_run:
            return await self._one(request, ConnectorProbeEvent, on_approval)
        return await self._one(request, ConnectorStatusEvent, on_approval)

    async def connectors_sign_in(
        self,
        *,
        name: str | None,
        url: str | None = None,
        title: str | None = None,
        auth: ConnectorAuth = "oauth",
        client_id: str | None = None,
        no_browser: bool = False,
    ) -> AsyncGenerator[Event, None]:
        """Add a connector (`url` given) or sign one in again, yielding events
        until `done` or `error`.

        A `connector.auth` event means the user must act: open its `url`, paste
        the address the browser ended on (`connectors_auth_paste`), or type a
        token (`connectors_auth_token`). `stop()` cancels the wait.
        """
        if url is None and name is None:
            raise ValueError("Give `url` to add a connector, or `name` to sign in.")
        request: Request = (
            ConnectorsLoginRequest(name=name or "", no_browser=no_browser)
            if url is None
            else ConnectorsAddRequest(
                url=url,
                name=name,
                title=title,
                auth=auth,
                client_id=client_id,
                no_browser=no_browser,
            )
        )
        async for event in self._stream(request):
            yield event

    async def connectors_logout(self, name: str) -> ConnectorStatusEvent:
        return await self._one(ConnectorsLogoutRequest(name=name), ConnectorStatusEvent)

    async def connectors_auth_paste(self, name: str, redirect_url: str) -> None:
        """Hand the pasted address to the waiting sign-in; raises on a bad one."""
        await self._expect_done(
            ConnectorsAuthPasteRequest(name=name, redirect_url=redirect_url)
        )

    async def connectors_auth_token(self, name: str, token: str) -> None:
        await self._expect_done(ConnectorsAuthTokenRequest(name=name, token=token))

    async def connectors_enable(
        self, name: str, *, on_approval: ApprovalHandler | None = None
    ) -> ConnectorStatusEvent:
        """Turn a connector on; a local one not yet approved asks first."""
        return await self._one(
            ConnectorsEnableRequest(name=name), ConnectorStatusEvent, on_approval
        )

    async def connectors_disable(self, name: str) -> ConnectorStatusEvent:
        return await self._one(
            ConnectorsDisableRequest(name=name), ConnectorStatusEvent
        )

    async def connectors_remove(self, name: str) -> None:
        await self._expect_done(ConnectorsRemoveRequest(name=name))

    async def connectors_reconnect(self, name: str) -> None:
        """Reconnect; the new status arrives on `host_events()`."""
        await self._expect_done(ConnectorsReconnectRequest(name=name))

    async def connectors_tool_policy(
        self, name: str, tool: str, setting: Literal["auto", "ask", "off", "default"]
    ) -> ConnectorsEvent:
        request = ConnectorsToolPolicyRequest(name=name, tool=tool, setting=setting)
        return await self._one(request, ConnectorsEvent)

    async def connectors_trust_hints(self, name: str, trust: bool) -> ConnectorsEvent:
        request = ConnectorsTrustHintsRequest(name=name, trust=trust)
        return await self._one(request, ConnectorsEvent)

    async def host_events(self) -> AsyncIterator[Event]:
        """Events not tied to a request, such as a connector dropping or returning."""
        self._ensure_reader()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[HOST_EVENT_RE] = queue
        try:
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
        finally:
            self._queues.pop(HOST_EVENT_RE, None)

    async def _one(
        self,
        request: Request,
        kind: type[_E],
        on_approval: ApprovalHandler | None = None,
    ) -> _E:
        """Send `request` and return its one `kind` event once it is `done`.

        An `approval` on this request goes to `on_approval`; with no handler it is
        answered `no`, so nothing runs that nobody approved.
        """
        self._ensure_reader()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        found: _E | None = None
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError(
                        f"Transport closed before {request.type!r} completed."
                    )
                if isinstance(event, kind):
                    found = event
                elif isinstance(event, ApprovalEvent):
                    answer, reason = (
                        await on_approval(event)
                        if on_approval is not None
                        else ("no", None)
                    )
                    await self.approve(
                        event.approval_id, answer, digest=event.digest, reason=reason
                    )
                elif isinstance(event, ErrorEvent):
                    raise ProtocolError(event.message, code=event.code)
                elif isinstance(event, DoneEvent):
                    if found is None:
                        raise ProtocolError(f"{request.type!r} sent no result.")
                    return found
        finally:
            self._queues.pop(request.id, None)

    async def _expect_done(self, request: Request) -> None:
        terminal = await self._request_terminal(request)
        if isinstance(terminal, ErrorEvent):
            raise ProtocolError(terminal.message, code=terminal.code)

    def tools_summary(self) -> list[ToolInfo]:
        """Return built-in tools descriptors for `/tools`."""
        return get_builtin_tools()

    def workspace_summary(self) -> WorkspaceSummary:
        """Return current workspace root and session-approved folders."""
        if self._workspace_getter is not None:
            return self._workspace_getter()
        return self._fallback_workspace

    def set_workspace(self, new_root: Path | str) -> WorkspaceSummary:
        """Switch the active workspace root and clear session grants."""
        if self._workspace_setter is not None:
            return self._workspace_setter(new_root)
        self._fallback_workspace = resolve_workspace_summary(
            new_root,
            home=self._home,
        )
        return self._fallback_workspace

    async def close(self) -> None:
        """Close the client and underlying transport."""
        if self._closed:
            return
        self._closed = True
        await self._transport.close()
        if self._reader_task is not None and not self._reader_task.done():
            await asyncio.gather(self._reader_task, return_exceptions=True)


__all__ = [
    "AldwinClient",
    "BootstrapResult",
    "ClientBootstrapError",
    "ClientTransport",
    "ConnectorCommandError",
    "InvalidSkillSummary",
    "KeyCheckResult",
    "MemorySummary",
    "NoteEntry",
    "OnboardingStatus",
    "PersonaSummary",
    "PlatformNotSupportedError",
    "ProtocolError",
    "SkillSummary",
    "SkillsReport",
    "ToolInfo",
    "WorkspaceError",
    "WorkspaceNotAllowedError",
    "WorkspaceNotFoundError",
    "WorkspaceSummary",
    "bootstrap",
    "check_openai_key",
    "connector_needs_approval",
    "connectors_add_command",
    "connectors_add_local_command",
    "connectors_enable_command",
    "connectors_find_configs",
    "connectors_import_command",
    "connectors_remove_command",
    "connectors_set_enabled",
    "connectors_summary",
    "verify_active_openai_key",
    "get_builtin_tools",
    "get_memory_summary",
    "get_onboarding_status",
    "get_persona_summary",
    "get_persona_text",
    "get_skills_report",
    "mark_whats_new_seen",
    "mask_secret",
    "open_in_process_client",
    "pop_profile_truncation_warning",
    "purge_home_data",
    "save_setup",
    "update_available_nudge",
    "whats_new_due",
]
