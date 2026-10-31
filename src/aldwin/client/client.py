"""Client API that Python doors use to communicate with the Aldwin host."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
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
    SkillsReport,
    SkillSummary,
    ToolInfo,
    WorkspaceError,
    WorkspaceNotAllowedError,
    WorkspaceNotFoundError,
    WorkspaceSummary,
    bootstrap_host,
    check_openai_key,
    connectors_add_command,
    connectors_remove_command,
    connectors_set_enabled,
    connectors_summary,
    get_builtin_tools,
    get_memory_summary,
    get_onboarding_status,
    get_persona_summary,
    get_persona_text,
    get_skills_report,
    list_available_models,
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
from aldwin.protocol import (
    HOST_EVENT_RE,
    MIN_SUPPORTED_VERSION,
    PROTOCOL_VERSION,
    ApprovalAnswer,
    ApproveRequest,
    ConnectorAuth,
    ConnectorProbeEvent,
    ConnectorsAddRequest,
    ConnectorsAuthPasteRequest,
    ConnectorsAuthTokenRequest,
    ConnectorsDisableRequest,
    ConnectorsEnableRequest,
    ConnectorsEvent,
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
    Request,
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
            self._fallback_model = name.strip()
        return result

    async def new_conversation(self) -> None:
        """Close the current session file and start a new conversation."""
        await self._transport.new_conversation()

    async def list_models(self) -> list[str]:
        """Return available OpenAI chat models for `/model`."""
        return await list_available_models(home=self._home)

    def persona_summary(self) -> PersonaSummary:
        """Return current persona text and path metadata for `/persona`."""
        return get_persona_summary(self._home)

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

    async def connectors_list(self) -> ConnectorsEvent:
        """Every connector, plus the `connectors.yaml` error if the file is bad."""
        return await self._one(ConnectorsListRequest(), ConnectorsEvent)

    async def connectors_add(
        self,
        *,
        url: str,
        name: str | None = None,
        title: str | None = None,
        auth: ConnectorAuth = "none",
        token: str | None = None,
        token_env: str | None = None,
        dry_run: bool = False,
    ) -> ConnectorProbeEvent | ConnectorStatusEvent:
        """Probe (`dry_run`) or add a connector.

        Raises `ProtocolError` with `code="auth_required"` when the server needs
        a token, or `code="connector"` with the message to show.
        """
        request = ConnectorsAddRequest(
            url=url,
            name=name,
            title=title,
            auth=auth,
            token=token,
            token_env=token_env,
            dry_run=dry_run,
        )
        if dry_run:
            return await self._one(request, ConnectorProbeEvent)
        return await self._one(request, ConnectorStatusEvent)

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

    async def connectors_enable(self, name: str) -> ConnectorStatusEvent:
        return await self._one(ConnectorsEnableRequest(name=name), ConnectorStatusEvent)

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

    async def _one(self, request: Request, kind: type[_E]) -> _E:
        """Send `request` and return its one `kind` event once it is `done`."""
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
    "connectors_add_command",
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
    "list_available_models",
    "mark_whats_new_seen",
    "mask_secret",
    "open_in_process_client",
    "pop_profile_truncation_warning",
    "purge_home_data",
    "save_setup",
    "update_available_nudge",
    "whats_new_due",
]
