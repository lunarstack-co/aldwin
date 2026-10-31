"""Pydantic v2 message models for the Aldwin protocol (v2 with v1 negotiation)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Final, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    model_validator,
)

PROTOCOL_VERSION: Final[int] = 2
VERSION: Final[int] = PROTOCOL_VERSION
MIN_SUPPORTED_VERSION: Final[int] = 1
MAX_APPROVAL_REASON_CHARS: Final[int] = 280

ApprovalAnswer = Literal["yes", "yes_session", "no"]
ApprovalTier = Literal["ask", "diff"]
GrantScope = Literal["file", "folder", "tool"]
HistoryRole = Literal["user", "assistant", "tool"]
DoneReason = Literal["answered", "stopped", "max_steps", "ok"]
TodoStatus = Literal["pending", "in_progress", "done", "dropped"]
SkillSource = Literal["default", "user", "user override"]
ErrorCode = Literal[
    "version_mismatch",
    "protocol",
    "busy",
    "unknown_approval",
    "unknown_model",
    "model_unavailable",
    "auth_failed",
    "rate_limited",
    "not_found",
    "internal",
    "connector",
    "auth_required",
]
ConnectorStatus = Literal["connecting", "connected", "failed", "disabled"]
ConnectorAuth = Literal["none", "bearer"]
ToolSetting = Literal["auto", "ask", "off"]

# `re` for events not tied to a request, such as a connector dropping.
HOST_EVENT_RE: Final[str] = "host"


def _new_request_id() -> str:
    """Generate a unique protocol request identifier."""
    return f"req_{uuid4().hex[:12]}"


class TokenUsage(BaseModel):
    """Token consumption reported on a completed turn."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class HistoryItem(BaseModel):
    """One rendered message or tool summary returned in a history event."""

    model_config = ConfigDict(extra="ignore")

    role: HistoryRole
    text: str
    at: str | None = None


class TodoItem(BaseModel):
    """One task item in a `todo.updated` event."""

    model_config = ConfigDict(extra="ignore")

    text: str
    status: TodoStatus = "pending"


DroppedReason = Literal["unknown", "limit"]


class SkillCatalogItem(BaseModel):
    """One skill descriptor in a `skills` event."""

    model_config = ConfigDict(extra="ignore")

    name: str
    description: str
    source: SkillSource = "default"
    flags: list[str] = Field(default_factory=list)
    last_used: str | None = None


class DroppedSkill(BaseModel):
    """One `@`-picked skill name dropped during host validation."""

    model_config = ConfigDict(extra="ignore")

    name: str
    reason: DroppedReason


class RequestBase(BaseModel):
    """Envelope shared by all requests sent from a door to the host."""

    model_config = ConfigDict(extra="ignore")

    v: int = PROTOCOL_VERSION
    id: str = Field(default_factory=_new_request_id, min_length=1)
    channel: str = "cli"
    sender: str = "cli"


class HelloRequest(RequestBase):
    """Initial version handshake sent before any other request."""

    type: Literal["hello"] = "hello"
    v_min: int = Field(default=MIN_SUPPORTED_VERSION, ge=1)
    v_max: int = Field(default=PROTOCOL_VERSION, ge=1)


class TurnRequest(RequestBase):
    """Start a turn with user text and optional `@`-picked skills."""

    type: Literal["turn", "user.message"] = "turn"
    text: str
    skills: list[str] = Field(default_factory=list)


class StopRequest(RequestBase):
    """Interrupt and cancel the currently running turn."""

    type: Literal["stop"] = "stop"


class ApproveRequest(RequestBase):
    """Answer a pending approval prompt (`yes`, `yes_session`, or `no`)."""

    type: Literal["approve"] = "approve"
    approval_id: str = Field(min_length=1)
    answer: ApprovalAnswer
    digest: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _normalise_reason(self) -> ApproveRequest:
        if self.answer != "no" or self.reason is None:
            self.reason = None
        else:
            cleaned = self.reason.strip()
            self.reason = cleaned[:MAX_APPROVAL_REASON_CHARS] if cleaned else None
        return self


class HistoryRequest(RequestBase):
    """Fetch recent conversation messages and interrupted status."""

    type: Literal["history"] = "history"
    limit: int = Field(default=20, ge=1, le=500)


class SetModelRequest(RequestBase):
    """Switch the active model for subsequent turns."""

    type: Literal["set_model"] = "set_model"
    name: str = Field(min_length=1)


class SkillsListRequest(RequestBase):
    """Request the active skill catalog from the host."""

    type: Literal["skills.list"] = "skills.list"


class SkillsShowRequest(RequestBase):
    """Request the effective body of one skill for `/skills show`."""

    type: Literal["skills.show"] = "skills.show"
    name: str = Field(min_length=1)


class TodoGetRequest(RequestBase):
    """Request the current session todo list from the host."""

    type: Literal["todo.get"] = "todo.get"


class ConnectorsListRequest(RequestBase):
    """List connectors; answered with a `connectors` event."""

    type: Literal["connectors.list"] = "connectors.list"


class ConnectorsAddRequest(RequestBase):
    """Add a connector, or with `dry_run` only probe it and report its tools."""

    type: Literal["connectors.add"] = "connectors.add"
    url: str = Field(min_length=1)
    name: str | None = None
    title: str | None = None
    auth: ConnectorAuth = "none"
    token: str | None = Field(default=None, repr=False)
    token_env: str | None = None
    dry_run: bool = False


class _ConnectorNameRequest(RequestBase):
    name: str = Field(min_length=1)


class ConnectorsRemoveRequest(_ConnectorNameRequest):
    """Delete a connector and its saved token."""

    type: Literal["connectors.remove"] = "connectors.remove"


class ConnectorsEnableRequest(_ConnectorNameRequest):
    """Turn a connector on and connect it."""

    type: Literal["connectors.enable"] = "connectors.enable"


class ConnectorsDisableRequest(_ConnectorNameRequest):
    """Turn a connector off; its tools leave the conversation at once."""

    type: Literal["connectors.disable"] = "connectors.disable"


class ConnectorsReconnectRequest(_ConnectorNameRequest):
    """Drop and reopen a connector's session."""

    type: Literal["connectors.reconnect"] = "connectors.reconnect"


class ConnectorsToolPolicyRequest(_ConnectorNameRequest):
    """Set one server tool to `auto`, `ask` or `off`, or back to its `default`."""

    type: Literal["connectors.tool_policy"] = "connectors.tool_policy"
    tool: str = Field(min_length=1)
    setting: Literal["auto", "ask", "off", "default"]


class ConnectorsTrustHintsRequest(_ConnectorNameRequest):
    """Trust (or stop trusting) the server's read-only hints."""

    type: Literal["connectors.trust_hints"] = "connectors.trust_hints"
    trust: bool


Request = Annotated[
    HelloRequest
    | TurnRequest
    | StopRequest
    | ApproveRequest
    | HistoryRequest
    | SetModelRequest
    | SkillsListRequest
    | SkillsShowRequest
    | TodoGetRequest
    | ConnectorsListRequest
    | ConnectorsAddRequest
    | ConnectorsRemoveRequest
    | ConnectorsEnableRequest
    | ConnectorsDisableRequest
    | ConnectorsReconnectRequest
    | ConnectorsToolPolicyRequest
    | ConnectorsTrustHintsRequest,
    Field(discriminator="type"),
]


class EventBase(BaseModel):
    """Envelope shared by all events sent from the host to a door."""

    model_config = ConfigDict(extra="ignore")

    v: int = PROTOCOL_VERSION
    type: Literal["event"] = "event"
    re: str = Field(min_length=1)
    channel: str = "cli"
    sender: str = "host"


class TextEvent(EventBase):
    """A streamed chunk of assistant text."""

    kind: Literal["text"] = "text"
    delta: str


class ToolCallEvent(EventBase):
    """Notification that the agent is invoking a tool."""

    kind: Literal["tool_call"] = "tool_call"
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    args_preview: str = ""
    summary: str = ""
    connector: str | None = None
    connector_title: str | None = None
    remote_tool: str | None = None
    reason: str | None = None


class ToolResultEvent(EventBase):
    """Outcome summary emitted after a tool call finishes."""

    kind: Literal["tool_result"] = "tool_result"
    call_id: str = Field(min_length=1)
    ok: bool
    summary: str = ""
    preview: str = ""
    duration_ms: int = Field(default=0, ge=0)


class ApprovalEvent(EventBase):
    """Host request asking the door for user approval before an action."""

    kind: Literal["approval"] = "approval"
    approval_id: str = Field(min_length=1)
    tool: str = ""
    tier: ApprovalTier = "ask"
    summary: str
    detail: str = ""
    grantable: bool = False
    grant_scope: GrantScope | None = None
    digest: str = ""
    risky: list[str] = Field(default_factory=list)
    connector: str | None = None
    connector_title: str | None = None
    remote_tool: str | None = None
    reason: str | None = None


class TodoUpdatedEvent(EventBase):
    """Live task checklist update emitted by the `todo` tool."""

    kind: Literal["todo.updated"] = "todo.updated"
    items: list[TodoItem] = Field(default_factory=list)
    done: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)


class SkillsEvent(EventBase):
    """Active skill catalog emitted by the host."""

    kind: Literal["skills"] = "skills"
    items: list[SkillCatalogItem] = Field(default_factory=list)


class SkillsUsedEvent(EventBase):
    """Resolved `@`-picked skills for the current turn."""

    kind: Literal["skills.used"] = "skills.used"
    names: list[str] = Field(default_factory=list)
    dropped: list[DroppedSkill] = Field(default_factory=list)


class SkillBodyEvent(EventBase):
    """Effective `SKILL.md` body for `/skills show <name>`."""

    kind: Literal["skill.body"] = "skill.body"
    name: str
    body: str


class NoticeEvent(EventBase):
    """Non-terminal informational notice (e.g. `skills reloaded`)."""

    kind: Literal["notice"] = "notice"
    message: str


class HistoryEvent(EventBase):
    """Rendered past messages and interrupted status for session resume."""

    kind: Literal["history"] = "history"
    messages: list[HistoryItem] = Field(default_factory=list)
    interrupted: bool = False


class StatusEvent(EventBase):
    """Non-terminal status notice (such as OpenAI retry progress)."""

    kind: Literal["status"] = "status"
    message: str


class DoneEvent(EventBase):
    """Terminal success event ending a request."""

    kind: Literal["done"] = "done"
    reason: DoneReason
    usage: TokenUsage | None = None
    max_steps: int | None = None


class ConnectorToolInfo(BaseModel):
    """One server tool as a door shows it."""

    name: str
    model_name: str
    title: str | None = None
    description: str = ""
    read_only: bool = False
    setting: ToolSetting | None = None
    effective: ToolSetting = "ask"


class ConnectorInfo(BaseModel):
    """One connector as a door lists it."""

    name: str
    title: str
    url: str
    enabled: bool = True
    status: ConnectorStatus = "disabled"
    auth: ConnectorAuth = "none"
    trust_read_only_hints: bool = True
    tools: list[ConnectorToolInfo] = Field(default_factory=list)
    error: str | None = None
    retry_in_s: int | None = None


class ConnectorsEvent(EventBase):
    """Every connector, plus the `connectors.yaml` error when the file is invalid."""

    kind: Literal["connectors"] = "connectors"
    items: list[ConnectorInfo] = Field(default_factory=list)
    error: str | None = None


class ConnectorStatusEvent(EventBase):
    """A connector's status changed."""

    kind: Literal["connector.status"] = "connector.status"
    name: str
    status: ConnectorStatus
    tools: int = 0
    error: str | None = None
    retry_in_s: int | None = None


class ConnectorProbeEvent(EventBase):
    """The dry-run result of `connectors.add`: what the server offers."""

    kind: Literal["connector.probe"] = "connector.probe"
    name: str
    title: str
    tools: list[ConnectorToolInfo] = Field(default_factory=list)
    read: int = 0
    write: int = 0


class ErrorEvent(EventBase):
    """Terminal failure event ending a request."""

    kind: Literal["error"] = "error"
    code: ErrorCode
    message: str


Event = Annotated[
    TextEvent
    | ToolCallEvent
    | ToolResultEvent
    | ApprovalEvent
    | TodoUpdatedEvent
    | SkillsEvent
    | SkillsUsedEvent
    | SkillBodyEvent
    | NoticeEvent
    | HistoryEvent
    | StatusEvent
    | ConnectorsEvent
    | ConnectorStatusEvent
    | ConnectorProbeEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="kind"),
]


def _message_discriminator(value: Any) -> str:
    """Route a raw dict or model instance to the `event` or `request` branch."""
    msg_type = (
        value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    )
    if msg_type == "event":
        return "event"
    return "request"


Message = Annotated[
    Annotated[Request, Tag("request")] | Annotated[Event, Tag("event")],
    Discriminator(_message_discriminator),
]


@lru_cache(maxsize=1)
def _request_adapter() -> TypeAdapter[Request]:
    return TypeAdapter(Request)


@lru_cache(maxsize=1)
def _event_adapter() -> TypeAdapter[Event]:
    return TypeAdapter(Event)


@lru_cache(maxsize=1)
def _message_adapter() -> TypeAdapter[Message]:
    return TypeAdapter(Message)


def parse_request(raw: str | bytes) -> Request:
    """Validate and parse a JSON request string or bytes into a `Request`."""
    return _request_adapter().validate_json(raw)


def parse_event(raw: str | bytes) -> Event:
    """Validate and parse a JSON event string or bytes into an `Event`."""
    return _event_adapter().validate_json(raw)


def parse_message(raw: str | bytes) -> Message:
    """Validate and parse any protocol JSON frame into a `Request` or `Event`."""
    return _message_adapter().validate_json(raw)
