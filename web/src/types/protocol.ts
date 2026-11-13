export const PROTOCOL_VERSION = 2;

type RequestBase = { v: number; id: string; channel: "web"; sender: "web" };

export type HelloRequest = RequestBase & { type: "hello"; v_min: number; v_max: number };
/** `apps` holds connector names picked with `/`, such as `linear`. */
export type TurnRequest = RequestBase & { type: "turn"; text: string; skills: string[]; apps?: string[] };
export type StopRequest = RequestBase & { type: "stop" };
export type ApprovalAnswer = "yes" | "yes_session" | "no";
export type ApprovalTier = "ask" | "diff";
export type GrantScope = "file" | "folder" | "tool";
export type ApproveRequest = RequestBase & {
  type: "approve";
  approval_id: string;
  answer: ApprovalAnswer;
  digest?: string | null;
  reason?: string | null;
};
export type HistoryRequest = RequestBase & { type: "history"; limit: number };
export type StatusRequest = RequestBase & { type: "status" };
export type SessionsNewRequest = RequestBase & { type: "sessions.new" };
export type SessionsListRequest = RequestBase & { type: "sessions.list" };
export type SessionsOpenRequest = RequestBase & { type: "sessions.open"; session_id: string };
export type TodoGetRequest = RequestBase & { type: "todo.get" };
export type SkillsListRequest = RequestBase & { type: "skills.list" };
export type SkillsShowRequest = RequestBase & { type: "skills.show"; name: string };
export type ModelsListRequest = RequestBase & { type: "models.list"; provider?: string; refresh?: boolean };
export type SetModelRequest = RequestBase & { type: "set_model"; name: string };
export type ProviderServerRequest = RequestBase & {
  type: "provider.server";
  name: string;
  base_url: string;
  key?: string;
  title?: string;
  local?: boolean;
};
export type ProviderDisconnectRequest = RequestBase & { type: "provider.disconnect"; provider: string };
export type SearchListRequest = RequestBase & { type: "search.list" };
export type SearchConnectRequest = RequestBase & { type: "search.connect"; provider: string; key?: string; url?: string };
export type SearchUseRequest = RequestBase & { type: "search.use"; provider: string };
export type SearchDisconnectRequest = RequestBase & { type: "search.disconnect"; provider: string };
export type ConnectorAuth = "none" | "bearer" | "oauth";
export type ConnectorsListRequest = RequestBase & { type: "connectors.list" };
export type ConnectorsAddRequest = RequestBase & {
  type: "connectors.add";
  url: string;
  name?: string;
  title?: string;
  auth: ConnectorAuth;
  no_browser: boolean;
};
type ConnectorNamed<T extends string> = RequestBase & { type: T; name: string };
export type ConnectorsEnableRequest = ConnectorNamed<"connectors.enable">;
export type ConnectorsDisableRequest = ConnectorNamed<"connectors.disable">;
export type ConnectorsRemoveRequest = ConnectorNamed<"connectors.remove">;
export type ConnectorsLoginRequest = ConnectorNamed<"connectors.login"> & { no_browser: boolean };
export type ConnectorsAuthPasteRequest = ConnectorNamed<"connectors.auth_paste"> & { redirect_url: string };
export type ConnectorsAuthTokenRequest = ConnectorNamed<"connectors.auth_token"> & { token: string };

export type Request =
  | HelloRequest
  | TurnRequest
  | StopRequest
  | ApproveRequest
  | HistoryRequest
  | StatusRequest
  | SessionsNewRequest
  | SessionsListRequest
  | SessionsOpenRequest
  | TodoGetRequest
  | SkillsListRequest
  | SkillsShowRequest
  | ModelsListRequest
  | SetModelRequest
  | ProviderServerRequest
  | ProviderDisconnectRequest
  | SearchListRequest
  | SearchConnectRequest
  | SearchUseRequest
  | SearchDisconnectRequest
  | ConnectorsListRequest
  | ConnectorsAddRequest
  | ConnectorsEnableRequest
  | ConnectorsDisableRequest
  | ConnectorsRemoveRequest
  | ConnectorsLoginRequest
  | ConnectorsAuthPasteRequest
  | ConnectorsAuthTokenRequest;

type Outbound<R> = R extends Request ? Omit<R, "v" | "channel" | "sender"> : never;
/** The envelope is added when the request is framed. */
export type OutboundRequest = Outbound<Request>;

export type HistoryRole = "user" | "assistant" | "tool";
export type HistoryItem = {
  role: HistoryRole;
  text: string;
  at?: string | null;
  /** Only user rows carry apps. */
  apps?: { name: string; title: string }[];
};
export type DoneReason = "answered" | "stopped" | "max_steps" | "ok";
export type TokenUsage = { input_tokens: number; output_tokens: number };
export type TodoStatus = "pending" | "in_progress" | "done" | "dropped";
export type TodoItem = { text: string; status: TodoStatus };
export type SkillSource = "default" | "user" | "user override";
/** A flag starting `error:` means the skill failed to load. */
export type SkillCatalogItem = {
  name: string;
  description: string;
  source: SkillSource;
  flags: string[];
  last_used: string | null;
};
/** Not in the catalogue, or past the per-turn limit. */
export type DroppedSkill = { name: string; reason: "unknown" | "limit" };
export type ProviderModelStatus = "ok" | "invalid_key" | "no_access" | "unreachable" | "not_running" | "not_configured";
export type ModelEntry = {
  ref: string;
  id: string;
  title: string;
  context_window: number | null;
  supports_tools: boolean | null;
};
export type ProviderModels = {
  name: string;
  title: string;
  local: boolean;
  configured: boolean;
  status: ProviderModelStatus;
  models: ModelEntry[];
};
/** Never carries a full key. */
export type SearchProviderItem = {
  name: string;
  title: string;
  kind: "local" | "hosted";
  connected: boolean;
  source: "env" | "file" | "config" | "detected" | null;
  detail: string;
  active: boolean;
  signup_url: string;
  check_note: string | null;
  env_var: string | null;
};
export type ConnectorStatus = "connecting" | "connected" | "failed" | "disabled" | "needs_auth";
export type ToolSetting = "auto" | "ask" | "off";
export type ConnectorToolInfo = {
  name: string;
  model_name: string;
  title: string | null;
  description: string;
  read_only: boolean;
  setting: ToolSetting | null;
  effective: ToolSetting;
};
export type ConnectorInfo = {
  name: string;
  title: string;
  url: string | null;
  enabled: boolean;
  status: ConnectorStatus;
  auth: ConnectorAuth;
  trust_read_only_hints: boolean;
  tools: ConnectorToolInfo[];
  error: string | null;
  retry_in_s: number | null;
  transport: "http" | "stdio" | "sse";
  command: string | null;
  note: string | null;
};
export type CatalogItem = {
  name: string;
  title: string;
  purpose: string;
  url: string;
  auth: ConnectorAuth;
  docs_url: string;
  keywords: string[];
  token_help_url: string | null;
  added: boolean;
  /** Absent from older hosts. */
  category?: string | null;
};
export type AuthMode = "browser" | "paste" | "token";

export type SessionMeta = {
  id: string;
  created_at: string;
  updated_at: string;
  model: string;
  title: string;
  interrupted: boolean;
};

export type TerminalDisplay = {
  kind: "terminal";
  command: string;
  cwd: string;
  stdout: string;
  stderr: string;
  exit_code: number | null;
  duration_ms: number;
  truncated: boolean;
};

export type DiffLine = {
  kind: "context" | "added" | "removed";
  text: string;
};

export type DiffDisplay = {
  kind: "diff";
  path: string;
  additions: number;
  deletions: number;
  lines: DiffLine[];
  truncated: boolean;
};

export type FileEntry = {
  path: string;
  kind: "file" | "folder";
  additions?: number | null;
  deletions?: number | null;
};

export type FilesDisplay = {
  kind: "files";
  root: string;
  entries: FileEntry[];
  truncated: boolean;
};

export type SearchHit = {
  title: string;
  url: string;
  snippet: string;
};

export type SearchDisplay = {
  kind: "search";
  query: string;
  results: SearchHit[];
  truncated?: boolean;
};

export type PageDisplay = {
  kind: "page";
  url: string;
  title: string;
  site: string;
  description: string;
};

export type TableColumn = {
  key: string;
  label: string;
};

export type TableDisplay = {
  kind: "table";
  columns: TableColumn[];
  rows: Record<string, string>[];
  truncated: boolean;
};

export type MemoryChange = {
  text: string;
  change: "added" | "replaced" | "removed";
};

export type MemoryDisplay = {
  kind: "memory";
  changes: MemoryChange[];
};

export type DeniedDisplay = {
  kind: "denied";
  rule: string;
  message: string;
};

export type GrantDisplay = {
  kind: "grant";
  scope: GrantScope;
  reach: string[];
};

export type Display =
  | TerminalDisplay
  | DiffDisplay
  | FilesDisplay
  | SearchDisplay
  | PageDisplay
  | TableDisplay
  | MemoryDisplay
  | DeniedDisplay
  | GrantDisplay;

export type DisplayKind = Display["kind"];

export type ContextSegment = {
  key: string;
  label: string;
  tokens: number;
};

export type ContextUsage = {
  limit: number;
  used: number;
  segments: ContextSegment[];
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
  display?: Display | null;
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
  context?: ContextUsage | null;
};
export type TodoUpdatedEvent = EventBase & {
  kind: "todo.updated";
  items: TodoItem[];
  done: number;
  total: number;
};
export type NoticeEvent = EventBase & { kind: "notice"; message: string };
export type SkillsEvent = EventBase & { kind: "skills"; items: SkillCatalogItem[] };
export type SkillsUsedEvent = EventBase & { kind: "skills.used"; names: string[]; dropped: DroppedSkill[] };
/** Dropped apps are unknown (not connected) or past the limit. */
export type AppsUsedEvent = EventBase & { kind: "apps.used"; names: string[]; dropped: DroppedSkill[] };
export type SkillBodyEvent = EventBase & { kind: "skill.body"; name: string; body: string };
export type ModelsEvent = EventBase & { kind: "models"; current: string; providers: ProviderModels[] };
export type SearchProvidersEvent = EventBase & {
  kind: "search.providers";
  setting: string;
  active: string | null;
  items: SearchProviderItem[];
};
export type ConnectorsEvent = EventBase & {
  kind: "connectors";
  items: ConnectorInfo[];
  error: string | null;
  catalog: CatalogItem[];
};
export type ConnectorStatusEvent = EventBase & {
  kind: "connector.status";
  name: string;
  status: ConnectorStatus;
  tools: number;
  error: string | null;
  retry_in_s: number | null;
};
/** `expires_in` is the seconds left to answer. */
export type ConnectorAuthEvent = EventBase & {
  kind: "connector.auth";
  name: string;
  title: string;
  mode: AuthMode;
  url: string | null;
  token_help_url: string | null;
  expires_in: number;
};
export type ErrorEvent = EventBase & { kind: "error"; code: string; message: string };
/** Answered with `approve`. */
export type ApprovalEvent = EventBase & {
  kind: "approval";
  approval_id: string;
  call_id?: string | null;
  tool?: string;
  tier?: ApprovalTier;
  summary: string;
  detail?: string;
  grantable?: boolean;
  grant_scope?: GrantScope | null;
  digest?: string;
  risky?: string[];
  connector?: string | null;
  connector_title?: string | null;
  remote_tool?: string | null;
  reason?: string | null;
  display?: Display | null;
};
/** Events the chat screen leaves alone. */
export type UnhandledEvent = EventBase & {
  kind: "connector.probe" | "connectors.import";
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
  | SkillsEvent
  | SkillsUsedEvent
  | AppsUsedEvent
  | SkillBodyEvent
  | ModelsEvent
  | SearchProvidersEvent
  | ConnectorsEvent
  | ConnectorStatusEvent
  | ConnectorAuthEvent
  | ApprovalEvent
  | UnhandledEvent;
