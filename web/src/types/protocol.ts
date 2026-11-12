/** The protocol messages the browser door exchanges with the host over the WebSocket. */

export const PROTOCOL_VERSION = 2;

type RequestBase = { v: number; id: string; channel: "web"; sender: "web" };

export type HelloRequest = RequestBase & { type: "hello"; v_min: number; v_max: number };
export type TurnRequest = RequestBase & { type: "turn"; text: string; skills: string[] };
export type StopRequest = RequestBase & { type: "stop" };
export type HistoryRequest = RequestBase & { type: "history"; limit: number };
export type StatusRequest = RequestBase & { type: "status" };
export type SessionsNewRequest = RequestBase & { type: "sessions.new" };
export type SessionsListRequest = RequestBase & { type: "sessions.list" };
export type SessionsOpenRequest = RequestBase & { type: "sessions.open"; session_id: string };
export type TodoGetRequest = RequestBase & { type: "todo.get" };

export type Request =
  | HelloRequest
  | TurnRequest
  | StopRequest
  | HistoryRequest
  | StatusRequest
  | SessionsNewRequest
  | SessionsListRequest
  | SessionsOpenRequest
  | TodoGetRequest;

type Outbound<R> = R extends Request ? Omit<R, "v" | "channel" | "sender"> : never;
/** A request as the page builds it; the envelope is added when it is framed. */
export type OutboundRequest = Outbound<Request>;

export type HistoryRole = "user" | "assistant" | "tool";
export type HistoryItem = { role: HistoryRole; text: string; at?: string | null };
export type DoneReason = "answered" | "stopped" | "max_steps" | "ok";
export type TokenUsage = { input_tokens: number; output_tokens: number };
export type TodoStatus = "pending" | "in_progress" | "done" | "dropped";
export type TodoItem = { text: string; status: TodoStatus };

export type SessionMeta = {
  id: string;
  created_at: string;
  updated_at: string;
  model: string;
  title: string;
  interrupted: boolean;
};

type EventBase = { v: number; type: "event"; re: string; channel: string; sender: string };

export type TextEvent = EventBase & { kind: "text"; delta: string };
export type ToolCallEvent = EventBase & {
  kind: "tool_call";
  call_id: string;
  name: string;
  args_preview: string;
  summary: string;
  connector?: string | null;
  connector_title?: string | null;
  remote_tool?: string | null;
  reason?: string | null;
};
export type ToolResultEvent = EventBase & {
  kind: "tool_result";
  call_id: string;
  ok: boolean;
  summary: string;
  preview: string;
  duration_ms: number;
};
export type HistoryEvent = EventBase & {
  kind: "history";
  messages: HistoryItem[];
  interrupted: boolean;
};
export type StatusEvent = EventBase & {
  kind: "status";
  message: string;
  turn_streamed_chunks: number;
  active_tool: string | null;
  partial_text: string | null;
};
export type SessionsEvent = EventBase & {
  kind: "sessions";
  items: SessionMeta[];
  current: SessionMeta | null;
};
export type DoneEvent = EventBase & {
  kind: "done";
  reason: DoneReason;
  usage?: TokenUsage | null;
  max_steps?: number | null;
};
export type TodoUpdatedEvent = EventBase & {
  kind: "todo.updated";
  items: TodoItem[];
  done: number;
  total: number;
};
export type NoticeEvent = EventBase & { kind: "notice"; message: string };
export type ErrorEvent = EventBase & { kind: "error"; code: string; message: string };
/** Events the chat screen leaves alone, approvals included. */
export type UnhandledEvent = EventBase & {
  kind:
    | "approval"
    | "skills"
    | "skills.used"
    | "skill.body"
    | "models"
    | "search.providers"
    | "connectors"
    | "connector.status"
    | "connector.auth"
    | "connector.probe"
    | "connectors.import";
};

export type Event =
  | TextEvent
  | ToolCallEvent
  | ToolResultEvent
  | HistoryEvent
  | StatusEvent
  | SessionsEvent
  | DoneEvent
  | TodoUpdatedEvent
  | NoticeEvent
  | ErrorEvent
  | UnhandledEvent;
