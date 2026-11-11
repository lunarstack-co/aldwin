"""Turn runner, request scope, approval broker, and host loop for Aldwin."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Literal, cast
from uuid import uuid4

from agentling import (
    Agent,
    AgentSession,
    FinalEvent,
    ModelContextLengthError,
    StepEvent,
    TextDelta,
    Usage,
)
from agentling import (
    ToolCallEvent as AgentlingToolCallEvent,
)
from agentling import (
    ToolResultEvent as AgentlingToolResultEvent,
)
from agentling.memory import FinalStep, TaskStep
from agentling.tools import ToolCallError

import aldwin
from aldwin.core.agent import (
    AnyAskFn,
    SessionContext,
    build_agent,
    restore_session,
    switch_session_model,
)
from aldwin.core.approval_log import ApprovalLogger, LogTier
from aldwin.core.connectors.catalog import catalog_entry, load_catalog
from aldwin.core.connectors.config import (
    ConnectorConfig,
    OAuthSettings,
    check_name,
    check_url,
    suggest_name,
    suggest_name_for_command,
)
from aldwin.core.connectors.errors import (
    ConnectorAuthRequired,
    ConnectorConfigError,
    ConnectorError,
    NeedsSignIn,
)
from aldwin.core.connectors.local import parse_command_line
from aldwin.core.connectors.oauth_messages import needs_sign_in
from aldwin.core.connectors.policy import tool_call_labels
from aldwin.core.connectors.state import ConnectorSnapshot
from aldwin.core.home import home_dir
from aldwin.core.models import (
    classify_model_error,
    is_local_model,
    parse_model_ref_parts,
    retry_status_message,
)
from aldwin.core.policy import (
    ApprovalAnswer as PolicyApprovalAnswer,
)
from aldwin.core.policy import (
    ApprovalRequest,
    PolicySession,
    compute_digest,
    sanitize_display_text,
)
from aldwin.core.providers.registry import parse_model_ref
from aldwin.core.settings import (
    ConfigError,
    config_secrets_fingerprint,
    load_config,
    load_secrets,
    update_config_model,
)
from aldwin.core.skills import (
    LoadedSkill,
    build_catalogue,
    build_task,
    resolve_picked_skills,
    skills_fingerprint,
    to_catalog_items,
)
from aldwin.core.skills_recent import load_skills_recent, record_skills_used
from aldwin.core.tools import documents as document_tools
from aldwin.core.tools import download as download_tools
from aldwin.core.tools import edits as edit_tools
from aldwin.core.tools import summarize_tool_result
from aldwin.core.tools.files import row_summary as file_row_summary
from aldwin.core.tools.files import running_summary as file_running_summary
from aldwin.core.tools.memory_tools import running_summary as memory_running_summary
from aldwin.core.tools.todo import todo_counts, validate_todo_items
from aldwin.core.tools.web_search.hosted import KeyCheck, KeyCheckResult
from aldwin.core.workspace import (
    Workspace,
    default_workspace_root,
    format_outside_display,
)
from aldwin.host.connectors import (
    AddResult,
    AuthPrompt,
    ConnectorAdmin,
    ConnectorEvent,
    ConnectorManager,
    ImportReport,
)
from aldwin.host.conversation_lock import ConversationLock
from aldwin.host.in_process import WorkspaceSummary
from aldwin.host.sessions import (
    MAX_PREVIEW_CHARS,
    SessionMetaSummary,
    SessionStore,
    StoredSession,
    session_file_fingerprint,
    session_has_turns,
    shorten_one_line,
)
from aldwin.host.transport import HostTransport
from aldwin.protocol import (
    HOST_EVENT_RE,
    MIN_SUPPORTED_VERSION,
    PROTOCOL_VERSION,
    ApprovalAnswer,
    ApprovalEvent,
    ApproveRequest,
    CatalogItem,
    ConnectorAuthEvent,
    ConnectorInfo,
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
    ConnectorToolInfo,
    DoneEvent,
    DoneReason,
    DroppedSkill,
    ErrorCode,
    ErrorEvent,
    Event,
    GrantScope,
    HelloRequest,
    HistoryEvent,
    HistoryRequest,
    ImportRowInfo,
    ImportSourceInfo,
    ModelEntry,
    ModelsEvent,
    ModelsListRequest,
    NoticeEvent,
    ProviderDisconnectRequest,
    ProviderKeyRequest,
    ProviderModels,
    ProviderModelStatus,
    ProviderServerRequest,
    Request,
    SearchConnectRequest,
    SearchDisconnectRequest,
    SearchListRequest,
    SearchProviderItem,
    SearchProvidersEvent,
    SearchUseRequest,
    SessionMetaItem,
    SessionsEvent,
    SessionsListRequest,
    SessionsNewRequest,
    SessionsOpenRequest,
    SetModelRequest,
    SkillBodyEvent,
    SkillsEvent,
    SkillsListRequest,
    SkillsShowRequest,
    SkillsUsedEvent,
    StatusEvent,
    StatusRequest,
    StopRequest,
    TextEvent,
    TodoGetRequest,
    TodoItem,
    TodoStatus,
    TodoUpdatedEvent,
    TokenUsage,
    ToolCallEvent,
    ToolResultEvent,
    TurnRequest,
    redact_pasted_secrets,
)
from aldwin.protocol.messages import SkillCatalogItem

logger = logging.getLogger("aldwin.host.runner")

PASTED_SECRET_NOTICE: Final[str] = (
    "That looks like a secret. I removed it before sending; "
    "use /connectors or the private prompt."
)
CIRCUIT_BREAKER_NOTICE: Final[str] = (
    "Stopped after 3 declined actions. Tell Aldwin what you'd like instead."
)
CONVERSATION_BUSY_MESSAGE: Final[str] = (
    "Another Aldwin window is answering in this conversation. "
    "Wait for it to finish, then try again."
)
# Lets a reloading browser tab come back and answer waiting approvals.
APPROVAL_RECONNECT_GRACE_S: Final[float] = 60.0
ATTACH_POLL_S: Final[float] = 0.25

AskFn = Callable[[str, str], Awaitable[bool]]
AgentBuilder = Callable[..., Agent]
EventSender = Callable[[Event], Awaitable[None]]


def format_args_preview(arguments: dict[str, Any]) -> str:
    """Format tool call arguments as a single line capped at 120 characters."""
    if not arguments:
        return ""
    if "new_text" in arguments and isinstance(arguments["new_text"], str):
        raw_text = arguments["new_text"].strip()
        line_count = len(raw_text.splitlines()) if raw_text else 0
        unit = "line" if line_count == 1 else "lines"
        return f"persona.md · {line_count} {unit}"
    for raw_key in ("command", "url", "name", "path"):
        if raw_key in arguments and isinstance(arguments[raw_key], str):
            return shorten_one_line(arguments[raw_key], MAX_PREVIEW_CHARS)
    for quoted_key in ("query", "text"):
        if quoted_key in arguments and isinstance(arguments[quoted_key], str):
            val = arguments[quoted_key]
            if len(arguments) == 1:
                return shorten_one_line(f'"{val}"', MAX_PREVIEW_CHARS)
            extras = [f"{k}={v}" for k, v in arguments.items() if k != quoted_key]
            return shorten_one_line(
                f'"{val}" ({", ".join(extras)})',
                MAX_PREVIEW_CHARS,
            )
    raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    return shorten_one_line(raw, MAX_PREVIEW_CHARS)


def preview_lines(content: str, *, limit: int = 12) -> str:
    """First `limit` lines of tool text, without one outer untrusted wrapper."""
    text = content
    match = re.match(r'<untrusted id="([^"]+)" source="[^"]*">\n', text)
    if match is not None:
        close = f'\n</untrusted id="{match.group(1)}">'
        start = match.end()
        end = text.find(close, start)
        if end != -1:
            text = text[start:end]
    lines = text.splitlines()[:limit]
    return sanitize_display_text("\n".join(lines))


def format_tool_summary(content: str) -> str:
    """Format the first non-empty line of tool output capped at 120 characters."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return shorten_one_line(stripped, MAX_PREVIEW_CHARS)
    return ""


def _to_token_usage(usage: Usage | None) -> TokenUsage | None:
    """Convert Agentling `Usage` to protocol `TokenUsage`."""
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=max(0, usage.input_tokens),
        output_tokens=max(0, usage.output_tokens),
    )


def classify_turn_error(
    exc: BaseException,
    *,
    model: str | None = None,
    provider_title: str | None = None,
    local: bool = False,
) -> tuple[ErrorCode, str]:
    """Map an Agentling exception to a protocol `(ErrorCode, message)`."""
    return classify_model_error(
        exc, model=model, provider_title=provider_title, local=local
    )


class RequestScope:
    """Enforces that every request `id` receives exactly one terminal event."""

    def __init__(
        self,
        req_id: str,
        send_event: EventSender,
        *,
        version: int = PROTOCOL_VERSION,
    ) -> None:
        self.req_id = req_id
        self._send_event = send_event
        self._version = version
        self._terminated = False

    @property
    def terminated(self) -> bool:
        """Return `True` once `done` or `error` has been emitted."""
        return self._terminated

    async def emit(self, event: Event) -> None:
        """Emit a non-terminal event carrying `re == self.req_id`."""
        if self._terminated:
            raise RuntimeError(
                f"Cannot emit {event.kind!r} after terminal event for {self.req_id!r}."
            )
        if isinstance(event, (DoneEvent, ErrorEvent)):
            raise RuntimeError("Use `done()` or `error()` for terminal events.")
        if event.re != self.req_id:
            raise ValueError(
                f"Event `re` ({event.re!r}) must match request id ({self.req_id!r})."
            )
        event.v = self._version
        await self._send_event(event)

    async def done(
        self,
        reason: DoneReason,
        usage: TokenUsage | None = None,
        max_steps: int | None = None,
    ) -> None:
        """Emit the single terminal `DoneEvent` for this request."""
        if self._terminated:
            raise RuntimeError(
                f"Terminal event already emitted for request {self.req_id!r}."
            )
        self._terminated = True
        await self._send_event(
            DoneEvent(
                v=self._version,
                re=self.req_id,
                reason=reason,
                usage=usage,
                max_steps=max_steps,
            )
        )

    async def error(self, code: ErrorCode, message: str) -> None:
        """Emit the single terminal `ErrorEvent` for this request."""
        if self._terminated:
            raise RuntimeError(
                f"Terminal event already emitted for request {self.req_id!r}."
            )
        self._terminated = True
        await self._send_event(
            ErrorEvent(v=self._version, re=self.req_id, code=code, message=message)
        )

    async def __aenter__(self) -> RequestScope:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc is not None:
            if isinstance(exc, asyncio.CancelledError):
                return False
            logger.exception("Unhandled error in request %s", self.req_id, exc_info=exc)
            if not self._terminated:
                await self.error(
                    "internal",
                    "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log.",
                )
            return True
        if not self._terminated:
            raise RuntimeError(
                f"Request {self.req_id!r} exited without emitting a terminal event."
            )
        return False


@dataclass(slots=True)
class _PendingApproval:
    """Tracked state for one outstanding approval prompt."""

    future: asyncio.Future[PolicyApprovalAnswer]
    digest: str
    tool: str
    target: str
    tier: LogTier


class ApprovalBroker:
    """Tracks pending `asyncio.Future` objects for tool approvals."""

    def __init__(
        self,
        send_event: EventSender,
        *,
        approval_log: ApprovalLogger | None = None,
        connector_labels: Callable[[str], tuple[str | None, str | None]] | None = None,
    ) -> None:
        self._send_event = send_event
        self._approval_log = approval_log
        self._connector_labels = connector_labels
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = asyncio.Lock()

    @property
    def has_pending(self) -> bool:
        """Return `True` if at least one approval is waiting for an answer."""
        return bool(self._pending)

    async def ask_request(
        self,
        re: str,
        request: ApprovalRequest,
        *,
        door_version: int = PROTOCOL_VERSION,
        door_connected: bool = True,
    ) -> PolicyApprovalAnswer:
        """Emit `ApprovalEvent` and await `ApprovalAnswer`."""
        if not door_connected or (door_version == 1 and request.tier == "diff"):
            return PolicyApprovalAnswer(
                answer="no",
                source="no_approver",
                reason=None,
            )

        async with self._lock:
            approval_id = f"ap_{uuid4().hex[:12]}"
            loop = asyncio.get_running_loop()
            future: asyncio.Future[PolicyApprovalAnswer] = loop.create_future()

            grant_scope: GrantScope | None = None
            if request.grantable and request.grant_key:
                if request.grant_key.startswith("file:"):
                    grant_scope = "file"
                elif request.grant_key.startswith("folder:"):
                    grant_scope = "folder"
                elif request.grant_key.startswith("tool:"):
                    grant_scope = "tool"

            self._pending[approval_id] = _PendingApproval(
                future=future,
                digest=request.digest,
                tool=request.tool,
                target=request.detail or request.summary,
                tier=request.tier,
            )
            try:
                if door_version == 1:
                    ev = ApprovalEvent(
                        v=1,
                        re=re,
                        approval_id=approval_id,
                        summary=request.summary,
                        detail=request.detail or "",
                    )
                else:
                    ev = ApprovalEvent(
                        v=door_version,
                        re=re,
                        approval_id=approval_id,
                        tool=request.tool,
                        tier=request.tier,
                        summary=request.summary,
                        detail=request.detail or "",
                        grantable=request.grantable,
                        grant_scope=grant_scope,
                        digest=request.digest,
                        risky=list(request.risky),
                    )
                    if request.connector is not None:
                        title, remote = (
                            self._connector_labels(request.tool)
                            if self._connector_labels is not None
                            else (None, None)
                        )
                        ev.connector = request.connector
                        ev.connector_title = title
                        ev.remote_tool = remote
                await self._send_event(ev)
                return replace(await future, approval_id=approval_id)
            finally:
                self._pending.pop(approval_id, None)

    async def ask(
        self,
        re: str,
        summary: str,
        detail: str = "",
        *,
        door_version: int = PROTOCOL_VERSION,
        door_connected: bool = True,
    ) -> bool:
        """Legacy boolean approval helper used by custom test tools."""
        digest = compute_digest("run_command", {"summary": summary}, detail)
        req = ApprovalRequest(
            tool="run_command",
            tier="ask",
            summary=summary,
            detail=detail,
            grantable=False,
            grant_key=None,
            risky=(),
            digest=digest,
        )
        ans = await self.ask_request(
            re,
            req,
            door_version=door_version,
            door_connected=door_connected,
        )
        return ans.answer in ("yes", "yes_session")

    def resolve(
        self,
        approval_id: str,
        answer: ApprovalAnswer,
        *,
        digest: str | None = None,
        reason: str | None = None,
        door_version: int = PROTOCOL_VERSION,
    ) -> Literal["ok", "unknown", "digest_mismatch"]:
        """Resolve a pending approval future.

        If `approval_id` is unknown or `digest` does not match, logs an `error`
        decision and leaves a mismatched pending approval waiting.
        """
        pending = self._pending.get(approval_id)
        if pending is None or pending.future.done():
            if self._approval_log is not None:
                self._approval_log.log_decision(
                    call_id=approval_id,
                    tool="unknown",
                    target=approval_id,
                    digest=digest,
                    tier="ask",
                    decision="blocked",
                    answer="none",
                    source="error",
                    reason="unknown approval_id",
                )
            return "unknown"

        if (
            door_version >= 2
            and pending.digest
            and digest is not None
            and digest != pending.digest
        ):
            if self._approval_log is not None:
                self._approval_log.log_decision(
                    call_id=approval_id,
                    tool=pending.tool or "unknown",
                    target=pending.target,
                    digest=digest,
                    tier=pending.tier,
                    decision="asked",
                    answer="none",
                    source="error",
                    reason="approval digest mismatch",
                )
            return "digest_mismatch"

        self._pending.pop(approval_id, None)
        effective_reason = reason if answer == "no" else None
        pending.future.set_result(
            PolicyApprovalAnswer(
                answer=answer,
                source="user",
                reason=effective_reason,
            )
        )
        return "ok"

    def cancel_all(self) -> None:
        """Resolve all pending approvals as user `no` (used on `stop` / `Esc`)."""
        for approval_id in list(self._pending.keys()):
            pending = self._pending.pop(approval_id, None)
            if pending is not None and not pending.future.done():
                pending.future.set_result(
                    PolicyApprovalAnswer(answer="no", source="user", reason=None)
                )

    def disconnect_all(self) -> None:
        """Resolve pending approvals as `no_approver` on disconnect."""
        for approval_id in list(self._pending.keys()):
            pending = self._pending.pop(approval_id, None)
            if pending is not None and not pending.future.done():
                pending.future.set_result(
                    PolicyApprovalAnswer(
                        answer="no",
                        source="no_approver",
                        reason=None,
                    )
                )


def _env_key_message(
    title: str, env_var: str | None, *, removed_saved_key: bool = False
) -> str:
    name = env_var or "the environment variable"
    removed = "The saved key was removed. " if removed_saved_key else ""
    return (
        f"{removed}{title}'s key comes from {name} in your environment. Remove it "
        "from your shell profile, then restart Aldwin."
    )


def key_check_error(
    title: str, provider: str, check: KeyCheckResult
) -> tuple[ErrorCode, str]:
    """User-facing reason a key or address was not connected."""
    detail = check.detail
    if provider == "searxng":
        if check.outcome is KeyCheck.UNEXPECTED:
            return (
                "invalid_request",
                "Enter an address starting with http:// or https://.",
            )
        return (
            "search_unavailable",
            f"Could not reach SearXNG ({detail}). Start it with JSON output on, "
            "or check the address.",
        )
    if check.outcome is KeyCheck.REJECTED:
        return (
            "auth_failed",
            f"{title} rejected this key ({detail}). Check it and try again.",
        )
    if check.outcome is KeyCheck.NO_CREDITS:
        return (
            "request_rejected",
            f"{title} accepted the key, but the account is out of credits "
            f"({detail}). Add credit, then connect again.",
        )
    if check.outcome is KeyCheck.RATE_LIMITED:
        return (
            "rate_limited",
            f"{title} is rate limiting this key ({detail}). "
            "Wait a minute and try again.",
        )
    if check.outcome is KeyCheck.UNREACHABLE:
        return (
            "search_unavailable",
            f"Could not reach {title} to check the key ({detail}). Check your "
            "connection and try again.",
        )
    return (
        "search_unavailable",
        f"{title} gave an unexpected answer while checking the key ({detail}). "
        "Try again in a minute.",
    )


class AldwinHost:
    """In-process pass that receives protocol requests and drives `AgentSession`."""

    def __init__(
        self,
        transport: HostTransport,
        session_store: SessionStore | None = None,
        agent_builder: AgentBuilder | None = None,
        *,
        initial_model: str | None = None,
        available_models: Sequence[str] | Callable[[], Sequence[str]] | None = None,
        persist_model: bool = True,
        home: Path | None = None,
        workspace: Workspace | Path | str | None = None,
        backend_transport: Any = None,
    ) -> None:
        self._transport = transport
        self._backend_transport = backend_transport
        self._home = (home if home is not None else home_dir()).expanduser().resolve()
        self._session_store = session_store or SessionStore(
            sessions_dir=self._home / "sessions"
        )
        self._agent_builder = agent_builder
        self._available_models = available_models
        self._persist_model = persist_model

        config = load_config(self._home)
        self._config = config
        self._secrets = load_secrets(self._home)
        self._provider_models_cache: dict[
            str, tuple[float, ProviderModelStatus, list[ModelEntry]]
        ] = {}
        default_model = initial_model or config.model
        self._stored_session: StoredSession = self._session_store.load_latest(
            default_model=default_model
        )
        self._model: str = initial_model or self._stored_session.meta.model

        if isinstance(workspace, Workspace):
            self._workspace = workspace
        elif workspace is not None:
            self._workspace = Workspace(workspace, home=self._home)
        else:
            self._workspace = Workspace(
                default_workspace_root(self._home), home=self._home
            )

        self._approval_log = ApprovalLogger(
            home=self._home,
            session_id_getter=lambda: self._stored_session.meta.id,
        )
        self._policy_session = PolicySession(
            workspace=self._workspace,
            home=self._home,
            on_circuit_breaker=self._on_circuit_breaker,
        )

        self._welcomed = False
        self._negotiated_version: int = PROTOCOL_VERSION
        self._active_turn_id: str | None = None
        self._active_turn_scope: RequestScope | None = None
        self._last_approval_end_time: float = 0.0
        self._circuit_breaker_tripped: bool = False
        self._turn_task: asyncio.Task[None] | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._todo: list[dict[str, str]] = list(self._stored_session.todo or [])
        self._host_flags: dict[str, bool] = {"skills_dirty": False}
        self._pending_turn_notices: list[str] = []
        self._skills_fingerprint: tuple[tuple[str, int, int], ...] = ()
        self._config_secrets_fingerprint: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._session_fingerprint = session_file_fingerprint(self._stored_session.path)
        self._grace_deadline: float | None = None
        self._attach_watch: asyncio.Task[None] | None = None
        self._attached_at_turn_start = False
        self._turn_stream: list[str] = []
        self._turn_tools: dict[str, str] = {}
        self._skill_catalogue: list[LoadedSkill] = []

        self._session_context = SessionContext(
            workspace=self._workspace,
            policy_session=self._policy_session,
            ask=self.ask_policy,
            approval_log=self._approval_log,
            home=self._home,
            secrets=load_secrets(self._home),
            persona_cap=config.limits.persona_chars,
            env_allow=tuple(config.run_command.env_allow),
            session_id=self._stored_session.meta.id,
            host_flags=self._host_flags,
            model_name=self._model,
        )

        self._approvals = ApprovalBroker(
            self._transport.send_event,
            approval_log=self._approval_log,
            connector_labels=lambda name: self._connectors.labels(name),
        )
        self._connectors = ConnectorManager(
            admin=ConnectorAdmin(self._home),
            policy_session=self._policy_session,
            ask=self.ask_policy,
            approval_log=self._approval_log,
            emit=self._emit_connector_event,
            loaded_cap=config.limits.connector_tools,
            workspace_root=lambda: self._workspace.root,
            turn_auth=self._auth_during_turn,
        )
        self._import_paths = list(config.connectors.import_paths)
        self._connectors_started = False
        # Owned add/enable/reconnect flows, keyed by request id; and the names
        # they are working on, so a second flow for the same name is refused.
        self._connector_tasks: dict[str, asyncio.Task[None]] = {}
        self._connector_flows: set[str] = set()
        self._connector_flow_names: dict[str, str] = {}
        self._stopped_flows: set[str] = set()
        self._agent: Agent = self._build_and_attach_agent(self._model)
        self._attach_session(
            restore_session(self._agent, self._stored_session.memory.dump_json())
        )
        self._refresh_skill_state_from_agent()

        self._transport.set_new_conversation_handler(self.new_conversation)

    def _todo_event(self, re_id: str) -> TodoUpdatedEvent:
        """Build a `todo.updated` event from the current host todo list."""
        done, total = todo_counts(self._todo)
        return TodoUpdatedEvent(
            re=re_id,
            items=[
                TodoItem(text=i["text"], status=cast(TodoStatus, i["status"]))
                for i in self._todo
            ],
            done=done,
            total=total,
        )

    def _persist_session(self, *, interrupted: bool, touch: bool = True) -> None:
        """Save the active session memory and todo list."""
        self._session_store.save(
            self._stored_session,
            self._agent_session.memory,
            interrupted=interrupted,
            todo=self._todo,
            touch=touch,
        )
        self._session_fingerprint = session_file_fingerprint(self._stored_session.path)

    def _on_circuit_breaker(self) -> None:
        """Interrupt `AgentSession` after 3 declined actions in one turn."""
        self._circuit_breaker_tripped = True
        self._agent_session.interrupt()

    @property
    def policy_session(self) -> PolicySession:
        """Return the active `PolicySession`."""
        return self._policy_session

    @property
    def workspace(self) -> Workspace:
        """Return the active `Workspace`."""
        return self._workspace

    @property
    def negotiated_version(self) -> int:
        """Return the protocol version negotiated during `hello` (`1` or `2`)."""
        return self._negotiated_version

    def workspace_summary(self) -> WorkspaceSummary:
        """Return workspace root and granted folders for `/workspace`."""
        folders = tuple(
            sorted(
                format_outside_display(f) for f in self._policy_session.granted_folders
            )
        )
        return WorkspaceSummary(
            root=self._workspace.root,
            display_root=self._workspace.display_root,
            granted_folders=folders,
        )

    def set_workspace(self, new_root: Path | str) -> WorkspaceSummary:
        """Switch the workspace root and clear session grants."""
        self._workspace.set_root(new_root, session=self._policy_session)
        return self.workspace_summary()

    def _default_build_agent(self, model_name: str, ask: AnyAskFn) -> Agent:
        config = load_config(self._home).model_copy(update={"model": model_name})
        secrets = load_secrets(self._home)
        self._session_context.secrets = secrets
        self._session_context.persona_cap = config.limits.persona_chars
        self._session_context.env_allow = tuple(config.run_command.env_allow)
        self._session_context.session_id = self._stored_session.meta.id
        self._session_context.model_name = model_name
        return build_agent(
            config,
            secrets,
            ask,
            home=self._home,
            workspace=self._workspace,
            policy_session=self._policy_session,
            approval_log=self._approval_log,
            host_flags=self._host_flags,
            connectors_block=self._connectors.context_block,
            on_retry=self.on_retry,
            session_context=self._session_context,
            notice_emitter=self._emit_sync_notice,
        )

    def _emit_sync_notice(self, message: str) -> None:
        """Route synchronous notices to the turn scope queue."""
        self._pending_turn_notices.append(message)

    async def _flush_pending_notices(self, req_id: str, scope: RequestScope) -> None:
        """Emit any pending synchronous notices (e.g. from context window trims)."""
        if not self._pending_turn_notices:
            return
        notices = list(self._pending_turn_notices)
        self._pending_turn_notices.clear()
        for message in notices:
            await scope.emit(NoticeEvent(re=req_id, message=message))

    def _attach_session(self, session: AgentSession) -> None:
        """Make `session` the live agent session and give it the connector tools.

        Every rebuild (startup restore, skills reload, new conversation, model
        switch) goes through here, so connector tools survive all of them.
        """
        self._agent_session: AgentSession = session
        self._connectors.attach(session)

    def _refresh_skill_state_from_agent(self) -> None:
        """Cache catalogue and disk fingerprint after an Agent build."""
        catalogue = getattr(self._agent, "skill_catalogue", None)
        if catalogue is None:
            catalogue = build_catalogue(
                self._home,
                version=aldwin.__version__,
                model=self._model,
            )
            setattr(self._agent, "skill_catalogue", catalogue)
        self._skill_catalogue = list(catalogue)
        self._skills_fingerprint = skills_fingerprint(self._home)
        self._host_flags["skills_dirty"] = False

    def _skills_event(self, re_id: str) -> SkillsEvent:
        """Build a `skills` event from the cached catalogue."""
        recent = load_skills_recent(self._home)
        items = [
            SkillCatalogItem.model_validate(row)
            for row in to_catalog_items(self._skill_catalogue, last_used=recent)
        ]
        return SkillsEvent(re=re_id, items=items)

    def _rebuild_agent(self) -> None:
        """Rebuild Agent from disk settings and skills, keeping the conversation."""
        raw = self._agent_session.memory.dump_json()
        self._agent = self._build_and_attach_agent(self._model)
        self._attach_session(restore_session(self._agent, raw))
        self._refresh_skill_state_from_agent()
        self._persist_session(interrupted=self._stored_session.meta.interrupted)

    def _maybe_rebuild_skills_from_disk(self) -> bool:
        """Rebuild if user skill files changed since the last catalogue build."""
        current = skills_fingerprint(self._home)
        if current == self._skills_fingerprint:
            return False
        self._rebuild_agent()
        return True

    def _maybe_reload_config_from_disk(self) -> bool:
        """Rebuild if ``config.yaml`` or ``secrets.yaml`` changed since the last build.

        Another door or an editor may have changed limits, providers or keys.
        The active model stays as it is: switching it is the user's choice in
        this conversation, not a side effect of another door's default.
        Raises ``ConfigError`` when the changed file is invalid.
        """
        if config_secrets_fingerprint(self._home) == self._config_secrets_fingerprint:
            return False
        self._config = load_config(self._home)
        self._secrets = load_secrets(self._home)
        self._rebuild_agent()
        return True

    def _build_and_attach_agent(self, model_name: str) -> Agent:
        """Build an `Agent` and attach the host's retry and session callbacks."""
        # Taken before the build reads the files, so a write during it is seen
        # as a change on the next turn rather than missed.
        fingerprint = config_secrets_fingerprint(self._home)
        if self._agent_builder is not None:
            agent = self._agent_builder(model_name, self.ask)
        else:
            agent = self._default_build_agent(model_name, self.ask_policy)

        ctx = getattr(agent, "session_context", None)
        if isinstance(ctx, SessionContext):
            self._session_context = ctx
            ctx.session_id = self._stored_session.meta.id

        retry = getattr(agent, "retry", None)
        if retry is not None and retry.on_retry is None:
            agent.retry = replace(retry, on_retry=self.on_retry)
        self._config_secrets_fingerprint = fingerprint
        return agent

    async def on_retry(
        self,
        attempt: int,
        max_retries: int,
        exc: BaseException,
        delay_s: float,
    ) -> None:
        """Emit a non-terminal `StatusEvent` when the model retries."""
        scope = self._active_turn_scope
        if scope is not None and not scope.terminated:
            message = retry_status_message(
                exc,
                attempt=attempt,
                max_retries=max_retries,
                delay_s=delay_s,
                model=self._model,
            )
            await scope.emit(self._turn_status_event(scope.req_id, message))

    @property
    def model(self) -> str:
        """Return the currently configured model name."""
        return self._model

    @property
    def stored_session(self) -> StoredSession:
        """Return the active `StoredSession`."""
        return self._stored_session

    async def start(self) -> None:
        """Start connectors in the background and the request dispatch loop."""
        if not self._connectors_started:
            self._connectors_started = True
            await self._connectors.start()
        if self._serve_task is None or self._serve_task.done():
            self._serve_task = asyncio.create_task(self._serve_loop())

    async def stop_host(self) -> None:
        """Cancel any active turn, close the transport, and stop the dispatch loop."""
        self._approvals.disconnect_all()
        if self._attach_watch is not None:
            self._attach_watch.cancel()
            await asyncio.gather(self._attach_watch, return_exceptions=True)
        flows = list(self._connector_tasks.values())
        for task in flows:
            task.cancel()
        await asyncio.gather(*flows, return_exceptions=True)
        await self._connectors.aclose()
        if self._turn_task is not None and not self._turn_task.done():
            self._agent_session.interrupt()
            self._turn_task.cancel()
            await asyncio.gather(self._turn_task, return_exceptions=True)
        await self._transport.close()
        if self._serve_task is not None and not self._serve_task.done():
            await asyncio.gather(self._serve_task, return_exceptions=True)
        self._approval_log.close()

    def _approval_door_connected(self) -> bool:
        """Return whether a door can answer approvals; starts and ends the grace."""
        if not self._welcomed or self._transport.closed:
            return False
        if self._transport.attached:
            self._grace_deadline = None
            return True
        if self._grace_deadline is None:
            busy = self._turn_in_flight() and self._attached_at_turn_start
            if not (busy or self._approvals.has_pending):
                return False
            self._grace_deadline = time.monotonic() + APPROVAL_RECONNECT_GRACE_S
        return time.monotonic() < self._grace_deadline

    def _ensure_attach_watch(self) -> None:
        """Start the attachment watch if it is not already running."""
        if self._attach_watch is None or self._attach_watch.done():
            self._attach_watch = asyncio.create_task(self._watch_attachment())

    async def _watch_attachment(self) -> None:
        """Deny waiting approvals once a dropped door's grace runs out."""
        # The transport has no detach callback, so poll while there is work.
        while True:
            await asyncio.sleep(ATTACH_POLL_S)
            if not (self._turn_in_flight() or self._approvals.has_pending):
                if not self._transport.attached:
                    self._grace_deadline = None
                return
            if not self._approval_door_connected() and self._approvals.has_pending:
                self._approvals.disconnect_all()

    def _reload_session_if_changed(self) -> bool:
        """Load the session file if another process saved it; False if unreadable."""
        path = self._stored_session.path
        fingerprint = session_file_fingerprint(path)
        if fingerprint in (self._session_fingerprint, (0, 0)):
            return True
        reloaded = self._session_store.load_file(path)
        if reloaded is None:
            return False
        reloaded.meta.model = self._model
        self._stored_session = reloaded
        self._todo = list(reloaded.todo or [])
        self._attach_session(restore_session(self._agent, reloaded.memory.dump_json()))
        self._session_fingerprint = fingerprint
        return True

    def _turn_status_event(self, re_id: str, message: str = "") -> StatusEvent:
        """Build a `status` event carrying the current turn's progress."""
        tools = list(self._turn_tools.values())
        return StatusEvent(
            re=re_id,
            message=message,
            turn_streamed_chunks=len(self._turn_stream),
            active_tool=tools[-1] if tools else None,
            partial_text="".join(self._turn_stream) or None,
        )

    async def ask_policy(self, request: ApprovalRequest) -> PolicyApprovalAnswer:
        """Injected into `PolicyWrappedTool` to request approval."""
        re_id = self._active_turn_id or "req_turn"
        connected = self._approval_door_connected()
        self._ensure_attach_watch()
        try:
            return await self._approvals.ask_request(
                re=re_id,
                request=request,
                door_version=self._negotiated_version,
                door_connected=connected,
            )
        finally:
            self._last_approval_end_time = time.monotonic()

    async def ask(
        self,
        summary_or_request: str | ApprovalRequest,
        detail: str = "",
    ) -> Any:
        """Support `ask(ApprovalRequest)` and legacy `ask(summary, detail)`."""
        if isinstance(summary_or_request, ApprovalRequest):
            return await self.ask_policy(summary_or_request)
        re_id = self._active_turn_id or "req_turn"
        connected = self._approval_door_connected()
        self._ensure_attach_watch()
        try:
            return await self._approvals.ask(
                re=re_id,
                summary=summary_or_request,
                detail=detail,
                door_version=self._negotiated_version,
                door_connected=connected,
            )
        finally:
            self._last_approval_end_time = time.monotonic()

    async def new_conversation(self) -> None:
        """Start a new conversation for the terminal's `/new`.

        Unlike `sessions.new`, a running turn is stopped first rather than
        refused, because the terminal user asked for it with the turn on screen.
        """
        self._approvals.cancel_all()
        task = self._turn_task
        if task is not None and not task.done():
            self._agent_session.interrupt()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._persist_session(interrupted=False)
        self._activate_session(self._landing_session())

    def _turn_in_flight(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    def _landing_session(self) -> StoredSession:
        """Return the empty conversation a new chat should use.

        Reuses the active conversation or the newest saved one while it has no
        turns, so repeated new chats never leave a trail of empty files.
        """
        active = self._stored_session
        if not session_has_turns(active.turns, active.meta.title):
            return active
        saved = self._session_store.list_metas(include_empty=True)
        if saved and not saved[0].has_turns:
            latest = self._session_store.load_id(saved[0].id)
            if latest is not None:
                return latest
        return self._session_store.create_new(model=self._model)

    def _activate_session(self, stored: StoredSession) -> None:
        """Make `stored` the active conversation; the caller checks it is idle."""
        if stored.meta.id == self._stored_session.meta.id:
            return
        self._persist_session(
            interrupted=self._stored_session.meta.interrupted, touch=False
        )
        self._policy_session.reset_turn()
        self._todo = list(stored.todo or [])
        # Later turns run on the host's model, so the conversation records it.
        stored.meta.model = self._model
        self._stored_session = stored
        self._session_fingerprint = session_file_fingerprint(stored.path)
        self._session_context.session_id = stored.meta.id
        self._connectors.reset_loaded()
        self._attach_session(restore_session(self._agent, stored.memory.dump_json()))

    @staticmethod
    def _session_item(meta: SessionMetaSummary | StoredSession) -> SessionMetaItem:
        source = meta.meta if isinstance(meta, StoredSession) else meta
        return SessionMetaItem(
            id=source.id,
            created_at=source.created_at,
            updated_at=source.updated_at,
            model=source.model,
            title=source.title,
            interrupted=source.interrupted,
        )

    async def _handle_sessions(
        self,
        scope: RequestScope,
        req: SessionsListRequest | SessionsOpenRequest | SessionsNewRequest,
    ) -> None:
        if isinstance(req, SessionsListRequest):
            items = [self._session_item(m) for m in self._session_store.list_metas()]
            await scope.emit(
                SessionsEvent(
                    re=req.id,
                    items=items,
                    current=self._session_item(self._stored_session),
                )
            )
            await scope.done("ok")
            return

        if self._turn_in_flight():
            await scope.error(
                "busy",
                "Aldwin is still working on your last request. "
                "Stop it before switching conversations.",
            )
            return

        if isinstance(req, SessionsOpenRequest):
            target = (
                self._stored_session
                if req.session_id == self._stored_session.meta.id
                else self._session_store.load_id(req.session_id)
            )
            if target is None:
                await scope.error("not_found", "That conversation no longer exists.")
                return
        else:
            target = self._landing_session()

        self._activate_session(target)
        await scope.emit(
            SessionsEvent(re=req.id, current=self._session_item(self._stored_session))
        )
        await scope.done("ok")

    async def _serve_loop(self) -> None:
        """Receive requests from the transport and dispatch them."""
        while True:
            req = await self._transport.receive_request()
            if req is None:
                self._approvals.disconnect_all()
                return
            await self._dispatch(req)

    async def _dispatch(self, req: Request) -> None:
        """Route one incoming `Request`."""
        if not self._welcomed and not isinstance(req, HelloRequest):
            async with RequestScope(
                req.id,
                self._transport.send_event,
                version=self._negotiated_version,
            ) as scope:
                await scope.error(
                    "protocol",
                    "Handshake required: send a `hello` request first.",
                )
            return

        if isinstance(req, TurnRequest):
            if self._turn_task is not None and not self._turn_task.done():
                async with RequestScope(
                    req.id,
                    self._transport.send_event,
                    version=self._negotiated_version,
                ) as scope:
                    await scope.error(
                        "busy",
                        "Aldwin is still working on your last request. "
                        "Press Esc to stop it first.",
                    )
                return

            turn_scope = RequestScope(
                req.id,
                self._transport.send_event,
                version=self._negotiated_version,
            )
            self._turn_task = asyncio.create_task(self._run_turn(turn_scope, req))
            return

        if isinstance(
            req,
            ConnectorsAddRequest
            | ConnectorsEnableRequest
            | ConnectorsReconnectRequest
            | ConnectorsLoginRequest,
        ):
            self._spawn_connector_request(req)
            return

        async with RequestScope(
            req.id,
            self._transport.send_event,
            version=self._negotiated_version,
        ) as scope:
            if isinstance(req, HelloRequest):
                await self._handle_hello(scope, req)
            elif isinstance(req, StopRequest):
                await self._handle_stop(scope)
            elif isinstance(req, ApproveRequest):
                await self._handle_approve(scope, req)
            elif isinstance(req, HistoryRequest):
                await self._handle_history(scope, req)
            elif isinstance(
                req, SessionsListRequest | SessionsOpenRequest | SessionsNewRequest
            ):
                await self._handle_sessions(scope, req)
            elif isinstance(req, SetModelRequest):
                await self._handle_set_model(scope, req)
            elif isinstance(req, ModelsListRequest):
                await self._handle_models_list(scope, req)
            elif isinstance(req, ProviderKeyRequest):
                await self._handle_provider_key(scope, req)
            elif isinstance(req, ProviderServerRequest):
                await self._handle_provider_server(scope, req)
            elif isinstance(req, ProviderDisconnectRequest):
                await self._handle_provider_disconnect(scope, req)
            elif isinstance(req, SearchListRequest):
                await self._handle_search_list(scope, req)
            elif isinstance(req, SearchConnectRequest):
                await self._handle_search_connect(scope, req)
            elif isinstance(req, SearchUseRequest):
                await self._handle_search_use(scope, req)
            elif isinstance(req, SearchDisconnectRequest):
                await self._handle_search_disconnect(scope, req)
            elif isinstance(req, SkillsListRequest):
                self._maybe_rebuild_skills_from_disk()
                await scope.emit(self._skills_event(req.id))
                await scope.done("ok")
            elif isinstance(req, SkillsShowRequest):
                self._maybe_rebuild_skills_from_disk()
                wanted = req.name.strip()
                match = next(
                    (s for s in self._skill_catalogue if s.name == wanted),
                    None,
                )
                if match is None or match.skill is None:
                    await scope.error(
                        "not_found",
                        f"No skill named {wanted!r}.",
                    )
                else:
                    await scope.emit(
                        SkillBodyEvent(
                            re=req.id,
                            name=match.name,
                            body=match.body,
                        )
                    )
                    await scope.done("ok")
            elif isinstance(req, TodoGetRequest):
                await scope.emit(self._todo_event(req.id))
                await scope.done("ok")
            elif isinstance(req, StatusRequest):
                await scope.emit(self._turn_status_event(req.id))
                await scope.done("ok")
            elif isinstance(
                req,
                ConnectorsListRequest
                | ConnectorsImportRequest
                | ConnectorsRemoveRequest
                | ConnectorsDisableRequest
                | ConnectorsToolPolicyRequest
                | ConnectorsTrustHintsRequest
                | ConnectorsLogoutRequest
                | ConnectorsAuthPasteRequest
                | ConnectorsAuthTokenRequest,
            ):
                await self._handle_connector_request(scope, req)

    async def _handle_connector_request(
        self,
        scope: RequestScope,
        req: ConnectorsListRequest
        | ConnectorsImportRequest
        | ConnectorsRemoveRequest
        | ConnectorsDisableRequest
        | ConnectorsToolPolicyRequest
        | ConnectorsTrustHintsRequest
        | ConnectorsLogoutRequest
        | ConnectorsAuthPasteRequest
        | ConnectorsAuthTokenRequest,
    ) -> None:
        """The quick connector requests: they only touch config and the session."""
        try:
            if isinstance(req, ConnectorsRemoveRequest):
                await self._connectors.remove(req.name)
            elif isinstance(req, ConnectorsImportRequest):
                report = await self._connectors.import_from_apps(
                    paths=req.paths,
                    dry_run=req.dry_run,
                    rename_conflicts=req.rename_conflicts,
                    extra_paths=self._import_paths,
                )
                await scope.emit(_import_event(req.id, report))
            elif isinstance(req, ConnectorsDisableRequest):
                snapshot = await self._connectors.disable(req.name)
                await scope.emit(_status_event(req.id, snapshot))
            elif isinstance(req, ConnectorsToolPolicyRequest):
                await self._connectors.set_tool_setting(req.name, req.tool, req.setting)
            elif isinstance(req, ConnectorsTrustHintsRequest):
                await self._connectors.set_trust_hints(req.name, req.trust)
            elif isinstance(req, ConnectorsLogoutRequest):
                snapshot = await self._connectors.sign_out(req.name)
                await scope.emit(_status_event(req.id, snapshot))
            elif isinstance(req, ConnectorsAuthPasteRequest):
                self._connectors.auth_paste(req.name, req.redirect_url)
            elif isinstance(req, ConnectorsAuthTokenRequest):
                self._connectors.auth_token(req.name, req.token)
        except ConnectorError as exc:
            await scope.error("connector", exc.message)
            return
        if isinstance(
            req,
            ConnectorsListRequest
            | ConnectorsToolPolicyRequest
            | ConnectorsTrustHintsRequest,
        ):
            await scope.emit(self._connectors_event(req.id))
        await scope.done("ok")

    def _spawn_connector_request(
        self,
        req: ConnectorsAddRequest
        | ConnectorsEnableRequest
        | ConnectorsReconnectRequest
        | ConnectorsLoginRequest,
    ) -> None:
        """Run a long connector flow as an owned task, so `approve` and `stop`
        are still served while it waits on the network.
        """
        scope = RequestScope(
            req.id, self._transport.send_event, version=self._negotiated_version
        )
        task = asyncio.create_task(self._run_connector_request(scope, req))
        self._connector_tasks[req.id] = task
        task.add_done_callback(lambda _t: self._connector_tasks.pop(req.id, None))

    async def _run_connector_request(
        self,
        scope: RequestScope,
        req: ConnectorsAddRequest
        | ConnectorsEnableRequest
        | ConnectorsReconnectRequest
        | ConnectorsLoginRequest,
    ) -> None:
        async with scope:
            try:
                name = self._flow_name(req)
            except ConnectorError as exc:
                await scope.error("connector", exc.message)
                return
            if name in self._connector_flows:
                await scope.error("busy", f"Already connecting {name}.")
                return
            self._connector_flows.add(name)
            self._connector_flow_names[req.id] = name

            async def on_auth(prompt: AuthPrompt) -> None:
                await scope.emit(_auth_event(req.id, prompt))

            async def approve(request: ApprovalRequest) -> PolicyApprovalAnswer:
                # Asked on this request's `re`, not the active turn's.
                self._ensure_attach_watch()
                return await self._approvals.ask_request(
                    re=req.id,
                    request=request,
                    door_version=self._negotiated_version,
                    door_connected=self._approval_door_connected(),
                )

            try:
                if isinstance(req, ConnectorsAddRequest):
                    result = await self._connectors.add(
                        _config_from(req, name),
                        token=req.token,
                        dry_run=req.dry_run,
                        no_browser=req.no_browser,
                        on_auth=on_auth,
                        approve=approve,
                    )
                    if result.notice is not None:
                        await scope.emit(NoticeEvent(re=req.id, message=result.notice))
                    if req.dry_run:
                        await scope.emit(_probe_event(req.id, result))
                    else:
                        await scope.emit(_status_event(req.id, result.snapshot))
                elif isinstance(req, ConnectorsEnableRequest):
                    snapshot = await self._connectors.enable(name, approve=approve)
                    await scope.emit(_status_event(req.id, snapshot))
                elif isinstance(req, ConnectorsLoginRequest):
                    snapshot = await self._connectors.login(
                        name, no_browser=req.no_browser, on_auth=on_auth
                    )
                    await scope.emit(_status_event(req.id, snapshot))
                else:
                    await self._connectors.reconnect(name)
            except (ConnectorAuthRequired, NeedsSignIn) as exc:
                await scope.error("auth_required", exc.message)
                return
            except ConnectorError as exc:
                await scope.error("connector", exc.message)
                return
            except asyncio.CancelledError:
                # Still one terminal event per request: `stop` or shutdown.
                if not scope.terminated:
                    if req.id in self._stopped_flows:
                        await scope.done("stopped")
                    else:
                        await scope.error("internal", "Aldwin is shutting down.")
                raise
            finally:
                self._connector_flows.discard(name)
                self._connector_flow_names.pop(req.id, None)
                self._stopped_flows.discard(req.id)
            await scope.done("ok")

    def _flow_name(
        self,
        req: ConnectorsAddRequest
        | ConnectorsEnableRequest
        | ConnectorsReconnectRequest
        | ConnectorsLoginRequest,
    ) -> str:
        """The connector a flow works on; for an add, checked or suggested."""
        if not isinstance(req, ConnectorsAddRequest):
            return req.name
        existing = [s.name for s in self._connectors.snapshots()]
        if req.url is not None and req.command is not None:
            raise ConnectorConfigError("Give either a URL or a command, not both.")
        if req.url is None and req.command is None:
            raise ConnectorConfigError("Give a URL or a command.")
        if req.command is not None:
            command, args = parse_command_line(req.command)
            suggested = suggest_name_for_command([command, *args], existing)
        else:
            check_url(req.url or "")
            suggested = suggest_name(req.url or "", existing)
        if req.name is None:
            return suggested
        check_name(req.name, existing)
        return req.name

    def _connectors_event(self, re_id: str) -> ConnectorsEvent:
        snapshots = self._connectors.snapshots()
        added = {s.name for s in snapshots}
        return ConnectorsEvent(
            re=re_id,
            items=[_connector_info(s) for s in snapshots],
            error=self._connectors.load_error,
            catalog=[
                CatalogItem(
                    name=e.name,
                    title=e.title,
                    purpose=e.purpose,
                    url=e.url,
                    auth=e.auth,
                    docs_url=e.docs_url,
                    keywords=list(e.keywords),
                    token_help_url=e.token_help_url,
                    added=e.name in added,
                )
                for e in load_catalog()
            ],
        )

    async def _auth_during_turn(self, prompt: AuthPrompt) -> None:
        """Ask the user to sign in on the turn whose tool call needs it."""
        scope = self._active_turn_scope
        if scope is None or scope.terminated or self._negotiated_version < 2:
            raise NeedsSignIn(needs_sign_in(prompt.title))
        await scope.emit(_auth_event(scope.req_id, prompt))

    async def _emit_connector_event(self, event: ConnectorEvent) -> None:
        """Send a connector change on the running turn, or on the host stream."""
        if not self._approval_door_connected() or self._negotiated_version < 2:
            return
        scope = self._active_turn_scope
        in_turn = (
            scope is not None
            and not scope.terminated
            and self._turn_task is not None
            and not self._turn_task.done()
        )
        re_id = scope.req_id if in_turn and scope is not None else HOST_EVENT_RE
        out: Event = (
            _status_event(re_id, event.snapshot)
            if event.kind == "status" and event.snapshot is not None
            else NoticeEvent(re=re_id, message=event.message)
        )
        out.v = self._negotiated_version
        if in_turn and scope is not None:
            await scope.emit(out)
        else:
            await self._transport.send_event(out)

    async def _handle_hello(self, scope: RequestScope, req: HelloRequest) -> None:
        common_min = max(MIN_SUPPORTED_VERSION, req.v_min)
        common_max = min(PROTOCOL_VERSION, req.v_max)
        if req.v_min <= req.v_max and common_min <= common_max:
            self._negotiated_version = common_max
            scope._version = common_max
            self._welcomed = True
            await scope.done("ok")
        else:
            await scope.error(
                "version_mismatch",
                f"Protocol version mismatch: client supports {req.v_min}..{req.v_max}, "
                f"host supports v{MIN_SUPPORTED_VERSION}..v{PROTOCOL_VERSION}.",
            )

    async def _handle_stop(self, scope: RequestScope) -> None:
        self._approvals.cancel_all()
        waiting = self._connectors.waiting_for_user()
        stopped = [
            task
            for req_id, task in self._connector_tasks.items()
            if self._connector_flow_names.get(req_id) in waiting
        ]
        for req_id, task in list(self._connector_tasks.items()):
            if task in stopped:
                self._stopped_flows.add(req_id)
                task.cancel()
        await asyncio.gather(*stopped, return_exceptions=True)
        if self._turn_task is not None and not self._turn_task.done():
            self._agent_session.interrupt()
            self._turn_task.cancel()
            await asyncio.gather(self._turn_task, return_exceptions=True)
        await scope.done("ok")

    async def _handle_approve(self, scope: RequestScope, req: ApproveRequest) -> None:
        status = self._approvals.resolve(
            req.approval_id,
            req.answer,
            digest=req.digest,
            reason=req.reason,
            door_version=self._negotiated_version,
        )
        if status == "ok":
            await scope.done("ok")
        elif status == "digest_mismatch":
            # ignore mismatched digest, log as error, keep waiting
            await scope.done("ok")
        else:
            await scope.error(
                "unknown_approval",
                f"Approval {req.approval_id!r} is unknown or has already finished.",
            )

    async def _handle_history(self, scope: RequestScope, req: HistoryRequest) -> None:
        messages = self._session_store.render_history(
            self._agent_session.memory,
            limit=req.limit,
            turns=self._stored_session.turns,
        )
        await scope.emit(
            HistoryEvent(
                re=req.id,
                messages=messages,
                interrupted=self._stored_session.meta.interrupted,
            )
        )
        await scope.done("ok")

    def _is_model_allowed(self, name: str) -> bool:
        cleaned = name.strip()
        if not cleaned:
            return False
        if self._available_models is None:
            return True
        allowed = (
            self._available_models()
            if callable(self._available_models)
            else self._available_models
        )
        return cleaned in allowed

    async def _handle_set_model(
        self,
        scope: RequestScope,
        req: SetModelRequest,
    ) -> None:
        if self._turn_task is not None and not self._turn_task.done():
            await scope.error(
                "busy",
                "Aldwin is still working on your last request. "
                "Press Esc to stop it before switching models.",
            )
            return

        cleaned = req.name.strip()
        try:
            model_ref = parse_model_ref(cleaned)
            canonical = str(model_ref)
        except Exception:
            await scope.error(
                "unknown_model",
                f"Model {cleaned!r} is not valid.",
            )
            return

        if not (self._is_model_allowed(canonical) or self._is_model_allowed(cleaned)):
            await scope.error(
                "unknown_model",
                f"Model {cleaned!r} is not available. "
                "Run `/model` to pick a supported model.",
            )
            return

        target_model = canonical
        if self._available_models is not None:
            allowed = (
                self._available_models()
                if callable(self._available_models)
                else self._available_models
            )
            if cleaned in allowed and canonical not in allowed:
                target_model = cleaned

        self._model = target_model
        self._stored_session.meta.model = target_model
        if self._persist_model:
            update_config_model(target_model, self._home)

        self._agent = self._build_and_attach_agent(self._model)
        self._attach_session(switch_session_model(self._agent_session, self._agent))
        self._refresh_skill_state_from_agent()
        self._persist_session(interrupted=self._stored_session.meta.interrupted)
        await scope.done("ok")

    async def _handle_models_list(
        self,
        scope: RequestScope,
        req: ModelsListRequest,
    ) -> None:
        from agentling.errors import ModelAuthError, ModelConnectionError

        from aldwin.core.providers.discovery import (
            probe_lmstudio_runtime,
            probe_ollama_runtime,
        )
        from aldwin.core.providers.factory import (
            get_backend,
            resolve_provider_key,
        )
        from aldwin.core.providers.registry import ProviderEntry, list_providers

        now = time.monotonic()
        config = load_config(self._home)
        self._config = config
        self._secrets = load_secrets(self._home)
        all_providers = list_providers(config)

        if req.provider:
            target_name = req.provider.strip().lower()
            providers = [p for p in all_providers if p.name == target_name]
            if not providers:
                await scope.error(
                    "not_found",
                    f"Unknown provider {req.provider!r}.",
                )
                return
        else:
            providers = all_providers

        async def _probe_provider(
            p: ProviderEntry,
        ) -> tuple[ProviderModelStatus, list[ModelEntry]]:
            prior_cached_models: list[ModelEntry] = []
            if p.name in self._provider_models_cache:
                _, _, prior_cached_models = self._provider_models_cache[p.name]

            if not req.refresh and p.name in self._provider_models_cache:
                ts, cached_status, cached_models = self._provider_models_cache[p.name]
                if now - ts < 600.0:
                    return cached_status, cached_models

            if p.local:
                if p.name == "ollama":
                    try:
                        status_res = await asyncio.wait_for(
                            probe_ollama_runtime(
                                timeout=5.0,
                                transport=self._backend_transport,
                            ),
                            timeout=5.0,
                        )
                        if status_res.running:
                            entries = [
                                ModelEntry(
                                    ref=f"ollama/{m.id}",
                                    id=m.id,
                                    title=m.title,
                                    context_window=m.context_window,
                                    supports_tools=m.supports_tools,
                                )
                                for m in status_res.models
                            ]
                            return "ok", entries
                        fallback = prior_cached_models or [
                            ModelEntry(
                                ref=f"ollama/{m.id}",
                                id=m.id,
                                title=m.title,
                                context_window=m.context_window,
                                supports_tools=m.supports_tools,
                            )
                            for m in status_res.models
                        ]
                        return "not_running", fallback
                    except Exception as exc:
                        logger.debug("Ollama probe failed: %s", exc)
                        return "not_running", prior_cached_models
                elif p.name == "lmstudio":
                    try:
                        status_res = await asyncio.wait_for(
                            probe_lmstudio_runtime(
                                timeout=5.0,
                                transport=self._backend_transport,
                            ),
                            timeout=5.0,
                        )
                        if status_res.running:
                            entries = [
                                ModelEntry(
                                    ref=f"lmstudio/{m.id}",
                                    id=m.id,
                                    title=m.title,
                                    context_window=m.context_window,
                                    supports_tools=m.supports_tools,
                                )
                                for m in status_res.models
                            ]
                            return "ok", entries
                        fallback = prior_cached_models or [
                            ModelEntry(
                                ref=f"lmstudio/{m.id}",
                                id=m.id,
                                title=m.title,
                                context_window=m.context_window,
                                supports_tools=m.supports_tools,
                            )
                            for m in status_res.models
                        ]
                        return "not_running", fallback
                    except Exception as exc:
                        logger.debug("LM Studio probe failed: %s", exc)
                        return "not_running", prior_cached_models
                else:
                    try:
                        backend = get_backend(
                            p,
                            config=config,
                            secrets=self._secrets,
                            home=self._home,
                            transport=self._backend_transport,
                        )
                        model_infos = await asyncio.wait_for(
                            backend.list_models(), timeout=5.0
                        )
                        entries = [
                            ModelEntry(
                                ref=f"{p.name}/{m.id}",
                                id=m.id,
                                title=m.title,
                                context_window=m.context_window,
                                supports_tools=m.supports_tools,
                            )
                            for m in model_infos
                        ]
                        return "ok", entries
                    except Exception as exc:
                        logger.debug("Local provider %s error: %s", p.name, exc)
                        return "not_running", prior_cached_models

            api_key = resolve_provider_key(p, secrets=self._secrets, home=self._home)
            if not api_key:
                return "not_configured", []

            try:
                backend = get_backend(
                    p,
                    config=config,
                    secrets=self._secrets,
                    home=self._home,
                    transport=self._backend_transport,
                )
                model_infos = await asyncio.wait_for(backend.list_models(), timeout=5.0)
                entries = [
                    ModelEntry(
                        ref=f"{p.name}/{m.id}",
                        id=m.id,
                        title=m.title,
                        context_window=m.context_window,
                        supports_tools=m.supports_tools,
                    )
                    for m in model_infos
                ]
                return "ok", entries
            except ModelAuthError:
                return "invalid_key", []
            except (asyncio.TimeoutError, TimeoutError):
                logger.debug("Provider %s timed out", p.name)
                return "unreachable", prior_cached_models
            except ModelConnectionError:
                logger.debug("Provider %s unreachable", p.name)
                return "unreachable", prior_cached_models
            except Exception as exc:
                logger.debug("Provider %s error: %s", p.name, exc)
                return "unreachable", prior_cached_models

        probe_results = await asyncio.gather(*[_probe_provider(p) for p in providers])

        provider_models: list[ProviderModels] = []
        for p, (status, models) in zip(providers, probe_results):
            self._provider_models_cache[p.name] = (now, status, models)
            is_configured = p.local or bool(
                resolve_provider_key(p, secrets=self._secrets, home=self._home)
            )
            provider_models.append(
                ProviderModels(
                    name=p.name,
                    title=p.title,
                    local=p.local,
                    configured=is_configured,
                    status=status,
                    models=models,
                )
            )

        await scope.emit(
            ModelsEvent(
                re=req.id,
                current=self._model,
                providers=provider_models,
            )
        )
        await scope.done("ok")

    async def _handle_provider_key(
        self,
        scope: RequestScope,
        req: ProviderKeyRequest,
    ) -> None:
        from aldwin.core.providers.base import AccessStatus
        from aldwin.core.providers.keys import save_provider_key
        from aldwin.core.providers.registry import get_provider

        provider = req.provider.strip().lower()
        key = req.key.strip()
        status = await save_provider_key(
            provider,
            key,
            home=self._home,
            config=self._config,
            transport=self._backend_transport,
        )
        if status == AccessStatus.OK:
            self._provider_models_cache.pop(provider, None)
            await scope.done("ok")
        elif status == AccessStatus.INVALID_KEY:
            entry = get_provider(provider, self._config)
            title = entry.title if entry else provider.title()
            await scope.error(
                "auth_failed",
                f"Your {title} key was rejected. Check the key and try again.",
            )
        else:
            entry = get_provider(provider, self._config)
            title = entry.title if entry else provider.title()
            await scope.error(
                "model_unavailable",
                f"Could not reach {title} to verify the key.",
            )

    async def _handle_provider_disconnect(
        self,
        scope: RequestScope,
        req: ProviderDisconnectRequest,
    ) -> None:
        from aldwin.core.providers.keys import KeyRemoval, remove_provider_key
        from aldwin.core.providers.registry import get_provider

        provider = req.provider.strip().lower()
        entry = get_provider(provider, self._config)
        title = entry.title if entry else provider.title()
        outcome, env_var = await asyncio.to_thread(
            remove_provider_key,
            provider,
            current_model=self._model,
            home=self._home,
            config=self._config,
        )
        if outcome is KeyRemoval.REMOVED:
            self._provider_models_cache.pop(provider, None)
            await scope.done("ok")
        elif outcome is KeyRemoval.IN_USE:
            await scope.error(
                "request_rejected",
                f"{title} runs the current model. Switch to another model first.",
            )
        elif outcome is KeyRemoval.ENV_ONLY:
            await scope.error("request_rejected", _env_key_message(title, env_var))
        else:
            await scope.error("not_found", f"{title} has no saved key.")

    async def _handle_search_list(
        self,
        scope: RequestScope,
        req: SearchListRequest,
    ) -> None:
        from dataclasses import asdict

        from aldwin.core.tools.web_search.connect import search_overview

        try:
            overview = await asyncio.to_thread(search_overview, self._home)
        except ConfigError as exc:
            await scope.error("invalid_request", str(exc))
            return
        await scope.emit(
            SearchProvidersEvent(
                re=req.id,
                setting=overview.setting,
                active=overview.active,
                items=[SearchProviderItem(**asdict(i)) for i in overview.items],
            )
        )
        await scope.done("ok")

    async def _handle_search_connect(
        self,
        scope: RequestScope,
        req: SearchConnectRequest,
    ) -> None:
        from aldwin.core.tools.web_search.connect import (
            connect_search_key,
            connect_searxng,
        )
        from aldwin.core.tools.web_search.shared import hosted_provider_classes

        provider = req.provider.strip().lower()
        hosted = hosted_provider_classes().get(provider)
        try:
            if provider == "searxng":
                title = "SearXNG"
                result = await connect_searxng(req.url or "", home=self._home)
            elif hosted is not None:
                title = hosted.name
                if not (req.key or "").strip():
                    await scope.error("invalid_request", f"Enter a {title} API key.")
                    return
                result = await connect_search_key(
                    provider,
                    req.key or "",
                    home=self._home,
                    transport=self._backend_transport,
                )
            else:
                await scope.error(
                    "invalid_request", f"Unknown search provider {req.provider!r}."
                )
                return
        except ConfigError as exc:
            await scope.error("invalid_request", str(exc))
            return

        if result.check.outcome is KeyCheck.OK:
            if result.env_override:
                await scope.emit(
                    NoticeEvent(
                        re=req.id,
                        message=(
                            f"{result.env_override} in your environment still "
                            "takes priority over this key."
                        ),
                    )
                )
            await scope.done("ok")
            return
        code, message = key_check_error(title, provider, result.check)
        await scope.error(code, message)

    async def _handle_search_use(
        self,
        scope: RequestScope,
        req: SearchUseRequest,
    ) -> None:
        from aldwin.core.tools.web_search import switch_search_provider

        try:
            result = await asyncio.to_thread(
                switch_search_provider, req.provider, self._home
            )
        except ConfigError as exc:
            await scope.error("invalid_request", str(exc))
            return
        if result.ok:
            await scope.done("ok")
        else:
            await scope.error("request_rejected", result.message)

    async def _handle_search_disconnect(
        self,
        scope: RequestScope,
        req: SearchDisconnectRequest,
    ) -> None:
        from aldwin.core.tools.web_search.connect import (
            DisconnectOutcome,
            disconnect_search,
        )
        from aldwin.core.tools.web_search.shared import hosted_provider_classes

        provider = req.provider.strip().lower()
        hosted = hosted_provider_classes().get(provider)
        if provider != "searxng" and hosted is None:
            await scope.error(
                "invalid_request", f"Unknown search provider {req.provider!r}."
            )
            return
        title = "SearXNG" if hosted is None else hosted.name
        try:
            result = await asyncio.to_thread(
                disconnect_search, provider, home=self._home
            )
        except ConfigError as exc:
            await scope.error("invalid_request", str(exc))
            return
        if result.outcome is DisconnectOutcome.REMOVED:
            await scope.done("ok")
        elif result.outcome is DisconnectOutcome.ENV_ONLY:
            await scope.error(
                "request_rejected",
                _env_key_message(
                    title, result.env_var, removed_saved_key=result.removed_saved_key
                ),
            )
        elif result.outcome is DisconnectOutcome.DETECTED:
            await scope.error(
                "request_rejected",
                "SearXNG was found running on this computer, so there is nothing "
                "to disconnect. Stop it, or pick another provider.",
            )
        else:
            await scope.error("not_found", f"{title} is not connected.")

    async def _handle_provider_server(
        self,
        scope: RequestScope,
        req: ProviderServerRequest,
    ) -> None:
        from aldwin.core.providers.base import AccessStatus
        from aldwin.core.providers.keys import (
            ServerAddressError,
            connect_provider_server,
        )

        name = req.name.strip().lower()
        base_url = req.base_url.strip().rstrip("/")
        try:
            status = await connect_provider_server(
                name,
                base_url,
                req.key,
                home=self._home,
                title=req.title,
                local=req.local,
                config=self._config,
                transport=self._backend_transport,
            )
        except ServerAddressError as exc:
            await scope.error("invalid_request", str(exc))
            return
        if status != AccessStatus.OK:
            await scope.error(
                "model_unavailable",
                f"Could not connect to server at {base_url}.",
            )
            return

        self._config = load_config(self._home)
        self._provider_models_cache.pop(name, None)
        await scope.done("ok")

    async def _run_turn(self, scope: RequestScope, req: TurnRequest) -> None:
        """Run one turn while holding this conversation's lock."""
        async with scope:
            try:
                self._maybe_reload_config_from_disk()
            except ConfigError as exc:
                await scope.error("invalid_request", str(exc))
                return
            lock = ConversationLock(self._home, self._stored_session.meta.id)
            if not lock.try_acquire():
                await scope.error("busy", CONVERSATION_BUSY_MESSAGE)
                return
            self._grace_deadline = None
            self._attached_at_turn_start = self._transport.attached
            self._turn_stream.clear()
            self._turn_tools.clear()
            self._ensure_attach_watch()
            try:
                # Under the lock, so another window's finished turn is complete.
                if not self._reload_session_if_changed():
                    await scope.error(
                        "internal",
                        f"Aldwin couldn't read the saved conversation "
                        f"{self._stored_session.path.name}. Start a new one with "
                        "/new; the file stays in ~/.aldwin/sessions.",
                    )
                    return
                await self._run_turn_steps(scope, req)
            finally:
                # No await: the door may send its next request right after `done`.
                self._grace_deadline = None
                self._attached_at_turn_start = False
                self._turn_stream.clear()
                self._turn_tools.clear()
                lock.release()

    async def _run_turn_steps(self, scope: RequestScope, req: TurnRequest) -> None:
        """Execute a turn and map Agentling events to protocol events."""
        self._active_turn_id = req.id
        self._active_turn_scope = scope
        self._circuit_breaker_tripped = False
        self._policy_session.reset_turn()
        from aldwin.core.tools.web_search import reset_web_search_turn_budget

        reset_web_search_turn_budget()
        self._maybe_rebuild_skills_from_disk()
        try:
            await self._connectors.reconcile()
        except Exception:
            logger.exception("connector reconcile failed; continuing the turn")
        for message in self._connectors.take_turn_notices():
            await scope.emit(NoticeEvent(re=req.id, message=message))
        await self._flush_pending_notices(req.id, scope)
        step_text_chunks: list[str] = []
        # Text streamed in the latest model step before tools cleared it.
        last_step_streamed = ""
        tool_start_times: dict[str, float] = {}
        tool_running: dict[str, str] = {}
        todo_call_items: dict[str, Any] = {}
        final_status: str = "completed"
        final_usage: Usage | None = None
        final_finish_reason: str | None = None

        picked: list[LoadedSkill] = []
        if self._negotiated_version >= 2:
            picked, dropped_pairs = resolve_picked_skills(
                list(req.skills),
                self._skill_catalogue,
            )
            dropped = [
                DroppedSkill(name=name, reason=reason) for name, reason in dropped_pairs
            ]
            used_names = [entry.name for entry in picked]
            if used_names or dropped:
                await scope.emit(
                    SkillsUsedEvent(
                        re=req.id,
                        names=used_names,
                        dropped=dropped,
                    )
                )
            if picked:
                record_skills_used(self._home, used_names)
        # The token stops here: the model, memory and session file get `text`.
        text = redact_pasted_secrets(req.text)
        if text != req.text:
            await scope.emit(NoticeEvent(re=req.id, message=PASTED_SECRET_NOTICE))
        task_text = build_task(text, picked)
        if self._stored_session.turns is None:
            self._stored_session.turns = []
        self._stored_session.turns.append(
            {
                "text": text,
                "skills": [entry.name for entry in picked],
            }
        )

        cold_load_task: asyncio.Task[None] | None = None
        if is_local_model(self._model):
            _, model_id_short = parse_model_ref_parts(self._model)

            async def _emit_cold_load_status() -> None:
                try:
                    await asyncio.sleep(3.0)
                    await scope.emit(
                        self._turn_status_event(
                            req.id, f"Loading {model_id_short} into memory…"
                        )
                    )
                except asyncio.CancelledError:
                    pass

            cold_load_task = asyncio.create_task(_emit_cold_load_status())

        try:
            stream_iter = self._agent_session.run(
                task_text,
                stream=True,
                reset=False,
            )
            can_trim_context = True
            while True:
                try:
                    async for ev in stream_iter:
                        await self._flush_pending_notices(req.id, scope)
                        if isinstance(ev, TextDelta):
                            if cold_load_task is not None and not cold_load_task.done():
                                cold_load_task.cancel()
                            if not step_text_chunks:
                                self._persist_session(interrupted=True)
                            step_text_chunks.append(ev.text)
                            self._turn_stream.append(ev.text)
                            await scope.emit(TextEvent(re=req.id, delta=ev.text))
                        elif isinstance(ev, AgentlingToolCallEvent):
                            if cold_load_task is not None and not cold_load_task.done():
                                cold_load_task.cancel()
                            if step_text_chunks:
                                last_step_streamed = "".join(step_text_chunks)
                                step_text_chunks.clear()
                            call = ev.tool_call
                            self._persist_session(interrupted=True)
                            self._connectors.note_tool_used(call.name)
                            self._policy_session.enqueue_step_call(
                                call.id,
                                call.name,
                                call.arguments,
                            )
                            tool_start_times[call.id] = time.monotonic()
                            if call.name == "todo":
                                todo_call_items[call.id] = call.arguments.get("items")
                            running = (
                                edit_tools.running_summary(
                                    call.name,
                                    call.arguments,
                                    self._workspace,
                                )
                                or file_running_summary(
                                    call.name,
                                    call.arguments,
                                    self._workspace,
                                )
                                or document_tools.running_summary(
                                    call.name,
                                    call.arguments,
                                    self._workspace,
                                )
                                or download_tools.running_summary(
                                    call.name,
                                    call.arguments,
                                    self._workspace,
                                )
                                or memory_running_summary(
                                    call.name,
                                    call.arguments,
                                    self._workspace,
                                )
                            )
                            tool_running[call.id] = running
                            self._turn_tools[call.id] = call.name
                            connector, tool_reason = tool_call_labels(call.name)
                            title, remote = self._connectors.labels(call.name)
                            await scope.emit(
                                ToolCallEvent(
                                    re=req.id,
                                    call_id=call.id,
                                    name=call.name,
                                    args_preview=format_args_preview(call.arguments),
                                    summary=running,
                                    connector=connector,
                                    connector_title=title,
                                    remote_tool=remote,
                                    reason=tool_reason,
                                )
                            )
                        elif isinstance(ev, AgentlingToolResultEvent):
                            res = ev.result
                            started = tool_start_times.pop(res.tool_call_id, None)
                            explicit_ms = getattr(res.content, "duration_ms", None)
                            if isinstance(explicit_ms, int):
                                duration_ms = max(0, explicit_ms)
                            elif started is not None:
                                effective_start = max(
                                    started, self._last_approval_end_time
                                )
                                duration_ms = max(
                                    0,
                                    int((time.monotonic() - effective_start) * 1000),
                                )
                            else:
                                duration_ms = 0
                            ok, summary = summarize_tool_result(
                                res.name,
                                res.content,
                                is_error=res.is_error,
                            )
                            running = tool_running.pop(res.tool_call_id, "")
                            self._turn_tools.pop(res.tool_call_id, None)
                            if res.name in edit_tools.EDIT_TOOL_NAMES:
                                summary = edit_tools.row_summary(
                                    running, summary, ok=ok
                                )
                            elif res.name in document_tools.DOCUMENT_TOOL_NAMES:
                                summary = document_tools.row_summary(
                                    running, summary, ok=ok
                                )
                            elif res.name in download_tools.DOWNLOAD_TOOL_NAMES:
                                summary = download_tools.row_summary(
                                    running, summary, ok=ok
                                )
                            else:
                                summary = file_row_summary(running, summary, ok=ok)
                            preview = ""
                            if ok:
                                preview = preview_lines(str(res.content))
                            if res.name == "todo" and ok:
                                raw_items = todo_call_items.pop(res.tool_call_id, None)
                                try:
                                    self._todo = validate_todo_items(raw_items)
                                except ToolCallError:
                                    pass
                                else:
                                    self._persist_session(interrupted=True)
                                    await scope.emit(self._todo_event(req.id))
                            else:
                                todo_call_items.pop(res.tool_call_id, None)
                            await scope.emit(
                                ToolResultEvent(
                                    re=req.id,
                                    call_id=res.tool_call_id,
                                    ok=ok,
                                    summary=summary,
                                    preview=preview,
                                    duration_ms=duration_ms,
                                )
                            )
                        elif isinstance(ev, StepEvent):
                            if step_text_chunks:
                                last_step_streamed = "".join(step_text_chunks)
                            step_text_chunks.clear()
                            self._policy_session.clear_step_calls()
                            self._persist_session(interrupted=True)
                        elif isinstance(ev, FinalEvent):
                            final_status = ev.status
                            final_usage = ev.usage
                            final_finish_reason = getattr(ev, "finish_reason", None)
                            # Plain-text endings were already streamed in this
                            # step; final_answer tool replies were not, unless
                            # the same text was streamed before the tool call.
                            if ev.answer:
                                step_streamed = "".join(step_text_chunks).strip()
                                prior = last_step_streamed.strip()
                                already = step_streamed or prior
                                if ev.answer.strip() != already:
                                    step_text_chunks.append(ev.answer)
                                    self._turn_stream.append(ev.answer)
                                    await scope.emit(
                                        TextEvent(re=req.id, delta=ev.answer)
                                    )
                    break
                except ModelContextLengthError:
                    task_indices = [
                        i
                        for i, s in enumerate(self._agent_session.memory.steps)
                        if isinstance(s, TaskStep)
                    ]
                    if len(task_indices) > 1 and can_trim_context:
                        can_trim_context = False
                        self._policy_session.clear_step_calls()
                        self._agent_session.memory.steps = list(
                            self._agent_session.memory.steps[task_indices[1] :]
                        )
                        step_text_chunks.clear()
                        self._turn_stream.clear()
                        last_step_streamed = ""
                        _, model_id = parse_model_ref_parts(self._model)
                        await scope.emit(
                            NoticeEvent(
                                re=req.id,
                                message=(
                                    f"This conversation is too long for "
                                    f"{model_id}. Aldwin trimmed older messages."
                                ),
                            )
                        )
                        stream_iter = self._agent_session.resume(stream=True)
                        continue
                    raise

            if final_status == "interrupted":
                self._flush_partial_text(step_text_chunks)
                self._persist_session(interrupted=False)
                if self._circuit_breaker_tripped and self._negotiated_version >= 2:
                    await scope.emit(
                        NoticeEvent(
                            re=req.id,
                            message=CIRCUIT_BREAKER_NOTICE,
                        )
                    )
                await scope.done("stopped", usage=_to_token_usage(final_usage))
                return

            reason: DoneReason = (
                "max_steps" if final_status == "max_steps" else "answered"
            )
            self._persist_session(interrupted=False)
            # Rebuild after the turn loop (never mid-tool) and emit
            # notice/skills before `done` so the door still receives them.
            if self._host_flags.get("skills_dirty"):
                self._rebuild_agent()
                await scope.emit(NoticeEvent(re=req.id, message="skills reloaded"))
                await scope.emit(self._skills_event(req.id))
            if final_finish_reason == "length":
                await scope.emit(
                    NoticeEvent(
                        re=req.id,
                        message=(
                            "The answer was cut off because it reached "
                            "the model's length limit."
                        ),
                    )
                )
            await self._flush_pending_notices(req.id, scope)
            await scope.done(
                reason,
                usage=_to_token_usage(final_usage),
                max_steps=self._agent.max_steps if reason == "max_steps" else None,
            )

        except asyncio.CancelledError:
            self._approvals.cancel_all()
            self._policy_session.clear_step_calls()
            self._flush_partial_text(step_text_chunks)
            self._persist_session(interrupted=False)
            if not scope.terminated:
                await scope.done("stopped")
            raise
        except Exception as exc:
            self._approvals.cancel_all()
            self._policy_session.clear_step_calls()
            logger.exception("Turn %s failed", req.id)
            self._persist_session(interrupted=True)
            code, msg = classify_turn_error(
                exc, model=self._model, local=is_local_model(self._model, exc=exc)
            )
            if not scope.terminated:
                await scope.error(code, msg)
        finally:
            if cold_load_task is not None and not cold_load_task.done():
                cold_load_task.cancel()
            self._active_turn_id = None
            self._active_turn_scope = None

    def _flush_partial_text(self, chunks: list[str]) -> None:
        """Append partial streamed text as a `FinalStep` when stopped mid-stream."""
        partial = "".join(chunks)
        if not partial:
            return
        steps = self._agent_session.memory.steps
        if not steps or not isinstance(steps[-1], FinalStep):
            self._agent_session.memory.add(FinalStep(answer=partial))
        chunks.clear()


def _config_from(req: ConnectorsAddRequest, name: str) -> ConnectorConfig:
    if req.command is not None:
        command, args = parse_command_line(req.command)
        return ConnectorConfig(
            name=name,
            command=command,
            args=args,
            env_allow=list(req.env_allow),
            cwd=req.cwd or "workspace",
            title=req.title,
        )
    auth = "bearer" if req.token or req.token_env else req.auth
    oauth: OAuthSettings | None = None
    if auth == "oauth":
        if req.client_id:
            oauth = OAuthSettings(client_id=req.client_id)
        else:
            entry = catalog_entry(name)
            if entry is not None and entry.url == req.url:
                oauth = entry.oauth
    return ConnectorConfig(
        name=name,
        url=req.url,
        title=req.title,
        auth=auth,
        token_env=req.token_env,
        oauth=oauth,
    )


def _auth_event(re_id: str, prompt: AuthPrompt) -> ConnectorAuthEvent:
    return ConnectorAuthEvent(
        re=re_id,
        name=prompt.name,
        title=prompt.title,
        mode=prompt.mode,
        url=prompt.url,
        token_help_url=prompt.token_help_url,
        expires_in=prompt.expires_in,
    )


def _tool_infos(snapshot: ConnectorSnapshot) -> list[ConnectorToolInfo]:
    return [
        ConnectorToolInfo(
            name=t.name,
            model_name=t.model_name,
            title=t.title,
            description=t.description,
            read_only=t.read_only,
            setting=t.setting,
            effective=t.effective,
        )
        for t in snapshot.tools
    ]


def _import_event(re_id: str, report: ImportReport) -> ConnectorsImportEvent:
    return ConnectorsImportEvent(
        re=re_id,
        found=[
            ImportSourceInfo(
                app=f.app,
                scope=f.scope,
                path=str(f.path),
                servers=f.servers,
                error=f.error,
            )
            for f in report.found
        ],
        rows=[
            ImportRowInfo(
                name=row.name,
                kind=row.kind,
                status=row.status,
                detail=row.detail,
                app=result.app,
            )
            for result in report.results
            for row in result.rows
        ],
        imported=sum(r.imported for r in report.results),
        values=sum(r.values for r in report.results),
        warnings=[*report.warnings, *(w for r in report.results for w in r.warnings)],
    )


def _connector_info(snapshot: ConnectorSnapshot) -> ConnectorInfo:
    return ConnectorInfo(
        name=snapshot.name,
        title=snapshot.title,
        url=snapshot.url,
        enabled=snapshot.enabled,
        status=snapshot.status,
        auth=snapshot.auth,
        trust_read_only_hints=snapshot.trust_read_only_hints,
        tools=_tool_infos(snapshot),
        error=snapshot.error,
        retry_in_s=snapshot.retry_in_s,
        transport=snapshot.transport,
        command=snapshot.command,
        note=snapshot.note,
    )


def _status_event(re_id: str, snapshot: ConnectorSnapshot) -> ConnectorStatusEvent:
    return ConnectorStatusEvent(
        re=re_id,
        name=snapshot.name,
        status=snapshot.status,
        tools=sum(1 for t in snapshot.tools if t.effective != "off"),
        error=snapshot.error,
        retry_in_s=snapshot.retry_in_s,
    )


def _probe_event(re_id: str, result: AddResult) -> ConnectorProbeEvent:
    return ConnectorProbeEvent(
        re=re_id,
        name=result.config.name,
        title=result.snapshot.title,
        tools=_tool_infos(result.snapshot),
        read=result.read,
        write=result.write,
    )
