"""Turn runner, request scope, approval broker, and host loop for Aldwin."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Literal, cast
from uuid import uuid4

from agentling import (
    Agent,
    AgentSession,
    FinalEvent,
    StepEvent,
    TextDelta,
)
from agentling import (
    ToolCallEvent as AgentlingToolCallEvent,
)
from agentling import (
    ToolResultEvent as AgentlingToolResultEvent,
)
from agentling.memory import FinalStep
from agentling.models import Usage
from agentling.tools import ToolCallError

import aldwin
from aldwin.core.agent import (
    AnyAskFn,
    build_agent,
    new_session,
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
)
from aldwin.core.connectors.errors import (
    ConnectorAuthRequired,
    ConnectorError,
    NeedsSignIn,
)
from aldwin.core.connectors.policy import tool_call_labels
from aldwin.core.connectors.state import ConnectorSnapshot
from aldwin.core.home import home_dir
from aldwin.core.models import (
    RetryingModel,
    classify_model_error,
    retry_status_message,
)
from aldwin.core.policy import (
    ApprovalAnswer as PolicyApprovalAnswer,
)
from aldwin.core.policy import (
    ApprovalRequest,
    PolicySession,
    PolicyWrappedTool,
    compute_digest,
    sanitize_display_text,
)
from aldwin.core.settings import load_config, load_secrets, update_config_model
from aldwin.core.skills import (
    LoadedSkill,
    build_catalogue,
    build_task,
    resolve_picked_skills,
    skills_fingerprint,
    to_catalog_items,
)
from aldwin.core.skills_recent import load_skills_recent, record_skills_used
from aldwin.core.tools import RememberTool, summarize_tool_result
from aldwin.core.tools import documents as document_tools
from aldwin.core.tools import download as download_tools
from aldwin.core.tools import edits as edit_tools
from aldwin.core.tools.files import row_summary as file_row_summary
from aldwin.core.tools.files import running_summary as file_running_summary
from aldwin.core.tools.memory_tools import running_summary as memory_running_summary
from aldwin.core.tools.todo import todo_counts, validate_todo_items
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
)
from aldwin.host.in_process import InProcessTransport, WorkspaceSummary
from aldwin.host.sessions import (
    MAX_PREVIEW_CHARS,
    SessionStore,
    StoredSession,
    shorten_one_line,
)
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
    NoticeEvent,
    Request,
    SetModelRequest,
    SkillBodyEvent,
    SkillsEvent,
    SkillsListRequest,
    SkillsShowRequest,
    SkillsUsedEvent,
    StatusEvent,
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
)
from aldwin.protocol.messages import SkillCatalogItem

logger = logging.getLogger("aldwin.host.runner")

CIRCUIT_BREAKER_NOTICE: Final[str] = (
    "Stopped after 3 declined actions. Tell Aldwin what you'd like instead."
)

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
) -> tuple[ErrorCode, str]:
    """Map an Agentling or OpenAI exception to a protocol `(ErrorCode, message)`."""
    return classify_model_error(exc, model=model)


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
                return await future
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


class AldwinHost:
    """In-process pass that receives protocol requests and drives `AgentSession`."""

    def __init__(
        self,
        transport: InProcessTransport,
        session_store: SessionStore | None = None,
        agent_builder: AgentBuilder | None = None,
        *,
        initial_model: str | None = None,
        available_models: Sequence[str] | Callable[[], Sequence[str]] | None = None,
        persist_model: bool = True,
        home: Path | None = None,
        workspace: Workspace | Path | str | None = None,
    ) -> None:
        self._transport = transport
        self._home = (home if home is not None else home_dir()).expanduser().resolve()
        self._session_store = session_store or SessionStore(
            sessions_dir=self._home / "sessions"
        )
        self._agent_builder = agent_builder
        self._available_models = available_models
        self._persist_model = persist_model

        default_model = initial_model or load_config(self._home).model
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
        self._skills_fingerprint: tuple[tuple[str, int, int], ...] = ()
        self._skill_catalogue: list[LoadedSkill] = []

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
        )
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

    def _persist_session(self, *, interrupted: bool) -> None:
        """Save the active session memory and todo list."""
        self._session_store.save(
            self._stored_session,
            self._agent_session.memory,
            interrupted=interrupted,
            todo=self._todo,
        )

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
        return build_agent(
            config,
            secrets,
            ask,
            home=self._home,
            workspace=self._workspace,
            policy_session=self._policy_session,
            approval_log=self._approval_log,
            session_id_getter=lambda: self._stored_session.meta.id,
            host_flags=self._host_flags,
            connectors_block=self._connectors.context_block,
        )

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

    def _rebuild_agent_for_skills(self) -> None:
        """Rebuild Agent and migrate Memory after skill changes."""
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
        self._rebuild_agent_for_skills()
        return True

    def _build_and_attach_agent(self, model_name: str) -> Agent:
        """Build an `Agent` and attach the host's retry and session callbacks."""
        if self._agent_builder is not None:
            agent = self._agent_builder(model_name, self.ask)
        else:
            agent = self._default_build_agent(model_name, self.ask_policy)

        if isinstance(agent.model, RetryingModel) and agent.model.on_retry is None:
            agent.model.on_retry = self.on_retry
        for tool_obj in agent._base_tools.values():
            inner = (
                tool_obj.inner_tool
                if isinstance(tool_obj, PolicyWrappedTool)
                else tool_obj
            )
            if isinstance(inner, RememberTool) and inner._session_id_getter is None:
                inner._session_id_getter = lambda: self._stored_session.meta.id
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
            await scope.emit(
                StatusEvent(
                    re=scope.req_id,
                    message=retry_status_message(
                        exc,
                        attempt=attempt,
                        max_retries=max_retries,
                        delay_s=delay_s,
                    ),
                )
            )

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

    async def ask_policy(self, request: ApprovalRequest) -> PolicyApprovalAnswer:
        """Injected into `PolicyWrappedTool` to request approval."""
        re_id = self._active_turn_id or "req_turn"
        connected = not self._transport.closed and self._welcomed
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
        connected = not self._transport.closed and self._welcomed
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
        """Close the current session file and start a fresh conversation."""
        self._approvals.cancel_all()
        if self._turn_task is not None and not self._turn_task.done():
            self._agent_session.interrupt()
            self._turn_task.cancel()
            await asyncio.gather(self._turn_task, return_exceptions=True)

        self._persist_session(interrupted=False)
        self._policy_session.reset_turn()
        self._todo = []
        self._stored_session = self._session_store.create_new(model=self._model)
        self._attach_session(new_session(self._agent))

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
            elif isinstance(req, SetModelRequest):
                await self._handle_set_model(scope, req)
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
            elif isinstance(
                req,
                ConnectorsListRequest
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

            try:
                if isinstance(req, ConnectorsAddRequest):
                    result = await self._connectors.add(
                        _config_from(req, name),
                        token=req.token,
                        dry_run=req.dry_run,
                        no_browser=req.no_browser,
                        on_auth=on_auth,
                    )
                    if result.notice is not None:
                        await scope.emit(NoticeEvent(re=req.id, message=result.notice))
                    if req.dry_run:
                        await scope.emit(_probe_event(req.id, result))
                    else:
                        await scope.emit(_status_event(req.id, result.snapshot))
                elif isinstance(req, ConnectorsEnableRequest):
                    snapshot = await self._connectors.enable(name)
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
        check_url(req.url)
        existing = [s.name for s in self._connectors.snapshots()]
        if req.name is None:
            return suggest_name(req.url, existing)
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

    async def _emit_connector_event(self, event: ConnectorEvent) -> None:
        """Send a connector change on the running turn, or on the host stream."""
        if self._transport.closed or not self._welcomed or self._negotiated_version < 2:
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
        if not self._is_model_allowed(cleaned):
            await scope.error(
                "unknown_model",
                f"Model {cleaned!r} is not available. "
                "Run `/model` to pick a supported model.",
            )
            return

        self._model = cleaned
        self._stored_session.meta.model = cleaned
        if self._persist_model:
            update_config_model(cleaned, self._home)

        self._agent = self._build_and_attach_agent(self._model)
        self._attach_session(switch_session_model(self._agent_session, self._agent))
        self._refresh_skill_state_from_agent()
        self._persist_session(interrupted=self._stored_session.meta.interrupted)
        await scope.done("ok")

    async def _run_turn(self, scope: RequestScope, req: TurnRequest) -> None:
        """Execute a turn and map Agentling events to protocol events."""
        async with scope:
            self._active_turn_id = req.id
            self._active_turn_scope = scope
            self._circuit_breaker_tripped = False
            self._policy_session.reset_turn()
            self._maybe_rebuild_skills_from_disk()
            try:
                await self._connectors.reconcile()
            except Exception:
                logger.exception("connector reconcile failed; continuing the turn")
            for message in self._connectors.take_auth_notices():
                await scope.emit(NoticeEvent(re=req.id, message=message))
            self._agent_session._interrupt.clear()
            step_text_chunks: list[str] = []
            streamed_before_tools: str = ""
            tool_start_times: dict[str, float] = {}
            tool_running: dict[str, str] = {}
            todo_call_items: dict[str, Any] = {}
            final_status: str = "completed"
            final_usage: Usage | None = None

            picked: list[LoadedSkill] = []
            if self._negotiated_version >= 2:
                picked, dropped_pairs = resolve_picked_skills(
                    list(req.skills),
                    self._skill_catalogue,
                )
                dropped = [
                    DroppedSkill(name=name, reason=reason)
                    for name, reason in dropped_pairs
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
            task_text = build_task(req.text, picked)
            if self._stored_session.turns is None:
                self._stored_session.turns = []
            self._stored_session.turns.append(
                {
                    "text": req.text,
                    "skills": [entry.name for entry in picked],
                }
            )

            try:
                async for ev in self._agent_session.run(
                    task_text,
                    stream=True,
                    reset=False,
                ):
                    if isinstance(ev, TextDelta):
                        if not step_text_chunks:
                            self._persist_session(interrupted=True)
                        step_text_chunks.append(ev.text)
                        await scope.emit(TextEvent(re=req.id, delta=ev.text))
                    elif isinstance(ev, AgentlingToolCallEvent):
                        if step_text_chunks:
                            streamed_before_tools = "".join(step_text_chunks)
                            step_text_chunks.clear()
                        call = ev.tool_call
                        self._persist_session(interrupted=True)
                        if call.name == "final_answer":
                            continue
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
                        if res.name == "final_answer":
                            if not res.is_error:
                                answer_text = str(res.content)
                                if answer_text and answer_text != streamed_before_tools:
                                    step_text_chunks.append(answer_text)
                                    await scope.emit(
                                        TextEvent(re=req.id, delta=answer_text)
                                    )
                            streamed_before_tools = ""
                            continue
                        started = tool_start_times.pop(res.tool_call_id, None)
                        explicit_ms = getattr(res.content, "duration_ms", None)
                        if isinstance(explicit_ms, int):
                            duration_ms = max(0, explicit_ms)
                        elif started is not None:
                            effective_start = max(started, self._last_approval_end_time)
                            duration_ms = max(
                                0, int((time.monotonic() - effective_start) * 1000)
                            )
                        else:
                            duration_ms = 0
                        ok, summary = summarize_tool_result(
                            res.name,
                            res.content,
                            is_error=res.is_error,
                        )
                        running = tool_running.pop(res.tool_call_id, "")
                        if res.name in edit_tools.EDIT_TOOL_NAMES:
                            summary = edit_tools.row_summary(running, summary, ok=ok)
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
                        step_text_chunks.clear()
                        streamed_before_tools = ""
                        self._policy_session.clear_step_calls()
                        self._persist_session(interrupted=True)
                    elif isinstance(ev, FinalEvent):
                        final_status = ev.status
                        final_usage = ev.usage

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
                    self._rebuild_agent_for_skills()
                    await scope.emit(NoticeEvent(re=req.id, message="skills reloaded"))
                    await scope.emit(self._skills_event(req.id))
                await scope.done(
                    reason,
                    usage=_to_token_usage(final_usage),
                    max_steps=self._agent.max_steps if reason == "max_steps" else None,
                )

            except asyncio.CancelledError:
                self._approvals.cancel_all()
                self._agent_session._interrupt.clear()
                self._policy_session.clear_step_calls()
                self._flush_partial_text(step_text_chunks)
                self._persist_session(interrupted=False)
                if not scope.terminated:
                    await scope.done("stopped")
                raise
            except Exception as exc:
                self._approvals.cancel_all()
                self._agent_session._interrupt.clear()
                self._policy_session.clear_step_calls()
                logger.exception("Turn %s failed", req.id)
                self._persist_session(interrupted=True)
                code, msg = classify_turn_error(exc, model=self._model)
                if not scope.terminated:
                    await scope.error(code, msg)
            finally:
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
