import type { Page, Route, WebSocketRoute } from "@playwright/test";

import type {
  AuthMode,
  ConnectorStatus,
  ContextUsage,
  Display,
  GrantScope,
  SkillCatalogItem,
} from "../../src/types/protocol";

import {
  LIMIT_MINIMUMS,
  SKILL_BODIES,
  connectorsFixture,
  modelsFixture,
  searchFixture,
  settingsFixture,
} from "./settings-fixtures";

/** A frame the page sent over the socket. */
export type Frame = { id: string; type: string } & Record<string, unknown>;
export type HistoryRow = {
  role: "user" | "assistant" | "tool";
  text: string;
  at?: string | null;
  apps?: { name: string; title: string }[];
};
export type SessionRow = {
  id: string;
  created_at: string;
  updated_at: string;
  model: string;
  title: string;
  interrupted: boolean;
};
export type TodoItem = { text: string; status: "pending" | "in_progress" | "done" | "dropped" };
export type SetupStatus = {
  keys_ok: boolean;
  host_ready: boolean;
  profile_done: boolean;
  profile_skipped: boolean;
  profile: { first_name: string; occupation: string; personality: string[] };
  model: string;
  provider: string;
  is_local: boolean;
};

export const CLEAR: SetupStatus = {
  keys_ok: true,
  host_ready: true,
  profile_done: true,
  profile_skipped: false,
  profile: { first_name: "Ada", occupation: "", personality: [] },
  model: "openai/gpt-4o",
  provider: "openai",
  is_local: false,
};

export const BUSY = "Aldwin is still working on your last request. Stop it before switching conversations.";
/** What the host says to a new turn while another is running. */
export const TURN_BUSY = "Aldwin is still working on your last request. Press Esc to stop it first.";
export const NOT_FOUND = "That conversation no longer exists.";

/** The host's per-turn cap on `@` skills (`MAX_PICKED_SKILLS`). */
export const MAX_PICKED_SKILLS = 3;

export function newSkill(name: string, extra: Partial<SkillCatalogItem> = {}): SkillCatalogItem {
  return { name, description: `Help with ${name}.`, source: "default", flags: [], last_used: null, ...extra };
}

export const SKILL_FIXTURE: SkillCatalogItem[] = [
  newSkill("files", { last_used: "2026-11-10T09:00:00Z" }),
  newSkill("memory"),
  newSkill("ship-notes", { source: "user", description: "Write release notes in our house style." }),
  newSkill("web-research", { last_used: "2026-11-11T09:00:00Z" }),
  newSkill("broken-skill", { flags: ["error:bad frontmatter"] }),
];

/** `n` plain skills named s01, s02 and so on, for the row cap. */
export function skillsOf(n: number): SkillCatalogItem[] {
  return Array.from({ length: n }, (_, i) => newSkill(`s${String(i + 1).padStart(2, "0")}`));
}

export function session(id: string, title: string, updatedAt = "2026-11-11T09:00:00Z", interrupted = false): SessionRow {
  return { id, created_at: updatedAt, updated_at: updatedAt, model: "openai/gpt-4o", title, interrupted };
}

type RestHandler = (route: Route, body: unknown) => Promise<void> | void;

/**
 * Plays Aldwin's host for one page: the WebSocket protocol and the REST
 * routes, with no Python running. Specs script it and read `sent`.
 */
export class FakeHost {
  status: SetupStatus = { ...CLEAR, profile: { ...CLEAR.profile } };
  history: HistoryRow[] = [];
  interrupted = false;
  sessions: SessionRow[] = [];
  current: SessionRow | null = null;
  todos: TodoItem[] = [];
  /** Every frame from every socket, in order. */
  sent: Frame[] = [];
  /** Every REST call, as `METHOD /path`. */
  calls: { method: string; path: string; body: unknown }[] = [];
  sockets: WebSocketRoute[] = [];
  socketUrls: string[] = [];
  activeTurn: string | null = null;
  /** Close each new socket with this code instead of letting it open. */
  refuseWith: { code: number; reason: string } | null = null;
  /** Answer the next `n` tickets with this status. */
  ticketFailures: { remaining: number; status: number } = { remaining: 0, status: 0 };
  openError: { code: string; message: string } | null = null;
  /** Leave `sessions.open` unanswered, as if the socket died before the reply. */
  holdOpen = false;
  /** Hold `hello` replies until `release()`. */
  holdHello = false;
  skills: SkillCatalogItem[] = SKILL_FIXTURE.map((s) => ({ ...s }));
  /** Answer `skills.list` with this error message instead of the catalogue. */
  skillsListError: string | null = null;
  /** Hold `skills.list` replies until `releaseSkills()`. */
  holdSkills = false;
  settings = settingsFixture();
  models = modelsFixture();
  search = searchFixture();
  connectors = connectorsFixture();
  skillBodies: Record<string, string> = { ...SKILL_BODIES };
  /** Every REST response body the fake sent, to prove no full key ever comes back. */
  responses: { path: string; body: unknown }[] = [];
  /** The id of the last `connectors.login` or `connectors.add`, for sign-in events tied to it. */
  /** Settings request types the fake answers with an error, to test the page's fallbacks. */
  failing = new Set<string>();
  lastSignInId: string | null = null;
  /** Optional error reply to the next `approve` frame (`unknown_approval` or digest mismatch). */
  approveError: { code: string; message: string } | null = null;
  /** Per-conversation saved JSON fixtures for `GET /v1/conversations/:id`. */
  conversations = new Map<string, { status?: number; delayMs?: number; body?: unknown }>();
  private heldSkills: (() => void)[] = [];
  private held: (() => void)[] = [];
  statusReply: { message: string; turn_streamed_chunks: number; active_tool: string | null; partial_text: string | null } = {
    message: "",
    turn_streamed_chunks: 0,
    active_tool: null,
    partial_text: null,
  };
  onTurn: ((frame: Frame) => void | Promise<void>) | null = null;
  rest = new Map<string, RestHandler>();
  private turnWaiters: ((frame: Frame) => void)[] = [];

  constructor(readonly page: Page) {}

  async install(): Promise<void> {
    await this.page.routeWebSocket(/\/v1\/ws/, (ws) => this.connect(ws));
    await this.page.route("**/v1/**", (route) => this.answerRest(route));
  }

  // --- socket ---

  private connect(ws: WebSocketRoute): void {
    this.sockets.push(ws);
    this.socketUrls.push(ws.url());
    if (this.refuseWith) {
      const { code, reason } = this.refuseWith;
      void ws.close({ code, reason });
      return;
    }
    ws.onMessage((message) => {
      const frame = JSON.parse(String(message)) as Frame;
      this.sent.push(frame);
      void this.reply(ws, frame);
    });
  }

  get socket(): WebSocketRoute {
    const ws = this.sockets[this.sockets.length - 1];
    if (!ws) throw new Error("no socket has been opened yet");
    return ws;
  }

  sentOfType(type: string): Frame[] {
    return this.sent.filter((frame) => frame.type === type);
  }

  /** Push one event to the page, as the host would. */
  emit(event: { re: string; kind: string } & Record<string, unknown>, ws: WebSocketRoute = this.socket): void {
    ws.send(JSON.stringify({ v: 2, type: "event", channel: "web", sender: "host", ...event }));
  }

  releaseSkills(): void {
    this.holdSkills = false;
    for (const go of this.heldSkills.splice(0)) go();
  }

  /** The host's fresh catalogue, as after `create_skill`. */
  emitSkills(items: SkillCatalogItem[]): void {
    this.skills = items;
    this.emit({ re: "host", kind: "skills", items });
  }

  /** What the host does with `turn.skills`: dedupe, drop unknown or broken names, cap at three. */
  private skillsUsed(frame: Frame, ws: WebSocketRoute): void {
    const picked = [...new Set((frame.skills as string[] | undefined) ?? [])];
    const names: string[] = [];
    const dropped: { name: string; reason: "unknown" | "limit" }[] = [];
    for (const name of picked) {
      const skill = this.skills.find((s) => s.name === name);
      if (!skill || skill.flags.some((f) => f.startsWith("error:"))) dropped.push({ name, reason: "unknown" });
      else if (names.length >= MAX_PICKED_SKILLS) dropped.push({ name, reason: "limit" });
      else names.push(name);
    }
    if (names.length || dropped.length) this.emit({ re: frame.id, kind: "skills.used", names, dropped }, ws);
  }

  /** What the host does with `turn.apps`: an app that is not connected (or has no tools) is unknown; at most three. */
  private appsUsed(frame: Frame, ws: WebSocketRoute): void {
    const picked = [...new Set((frame.apps as string[] | undefined) ?? [])];
    if (picked.length === 0) return;
    const connected = new Set(
      this.connectors.items.filter((c) => c.enabled && c.status === "connected" && c.tools.length > 0).map((c) => c.name),
    );
    const names: string[] = [];
    const dropped: { name: string; reason: "unknown" | "limit" }[] = [];
    for (const name of picked) {
      if (!connected.has(name)) dropped.push({ name, reason: "unknown" });
      else if (names.length >= MAX_PICKED_SKILLS) dropped.push({ name, reason: "limit" });
      else names.push(name);
    }
    this.emit({ re: frame.id, kind: "apps.used", names, dropped }, ws);
  }

  release(): void {
    this.holdHello = false;
    for (const go of this.held.splice(0)) go();
  }

  private refuseWhileBusy(ws: WebSocketRoute, frame: Frame): boolean {
    if (this.activeTurn === null) return false;
    this.emit({ re: frame.id, kind: "error", code: "busy", message: BUSY }, ws);
    return true;
  }

  private async reply(ws: WebSocketRoute, frame: Frame): Promise<void> {
    const done = () => this.emit({ re: frame.id, kind: "done", reason: "ok" }, ws);
    switch (frame.type) {
      case "hello": {
        const go = () => done();
        if (this.holdHello) this.held.push(go);
        else go();
        break;
      }
      case "history": {
        const messages =
          this.history.length > 0
            ? this.history
            : this.current
              ? (this.histories.get(this.current.id) ?? [])
              : [];
        this.emit({ re: frame.id, kind: "history", messages, interrupted: this.interrupted }, ws);
        done();
        break;
      }
      case "sessions.list":
        this.emit({ re: frame.id, kind: "sessions", items: this.sessions, current: this.current }, ws);
        done();
        break;
      case "sessions.new":
        if (this.refuseWhileBusy(ws, frame)) break;
        this.current = null;
        this.history = [];
        this.todos = [];
        this.emit({ re: frame.id, kind: "sessions", items: [], current: null }, ws);
        done();
        break;
      case "sessions.open": {
        if (this.holdOpen) break;
        if (this.refuseWhileBusy(ws, frame)) break;
        if (this.openError) {
          this.emit({ re: frame.id, kind: "error", ...this.openError }, ws);
          this.openError = null;
          break;
        }
        const found = this.sessions.find((row) => row.id === frame.session_id);
        if (!found) {
          this.emit({ re: frame.id, kind: "error", code: "not_found", message: NOT_FOUND }, ws);
          break;
        }
        this.current = found;
        this.history = this.historyFor(found);
        this.emit({ re: frame.id, kind: "sessions", items: [], current: found }, ws);
        done();
        break;
      }
      case "status":
        this.emit({ re: frame.id, kind: "status", ...this.statusReply }, ws);
        done();
        break;
      case "skills.list": {
        const go = () => {
          if (this.skillsListError) {
            this.emit({ re: frame.id, kind: "error", code: "internal", message: this.skillsListError }, ws);
            return;
          }
          this.emit({ re: frame.id, kind: "skills", items: this.skills }, ws);
          done();
        };
        if (this.holdSkills) this.heldSkills.push(go);
        else go();
        break;
      }
      case "todo.get":
        this.emit({ re: frame.id, kind: "todo.updated", ...this.todoCounts() }, ws);
        done();
        break;
      case "stop":
        done();
        if (this.activeTurn !== null) {
          this.emit({ re: this.activeTurn, kind: "done", reason: "stopped" }, ws);
          this.activeTurn = null;
        }
        break;
      case "approve": {
        if (this.approveError) {
          this.emit({ re: frame.id, kind: "error", ...this.approveError }, ws);
          this.approveError = null;
          break;
        }
        done();
        break;
      }
      case "turn":
        // Like the host: one turn at a time.
        if (this.activeTurn !== null) {
          this.emit({ re: frame.id, kind: "error", code: "busy", message: TURN_BUSY }, ws);
          break;
        }
        this.activeTurn = frame.id;
        this.skillsUsed(frame, ws);
        this.appsUsed(frame, ws);
        if (this.current === null) {
          const row = session(`s${this.sessions.length + 1}`, String(frame.text), "2026-11-11T10:00:00Z");
          this.sessions = [row, ...this.sessions];
          this.current = row;
        }
        for (const waiter of this.turnWaiters.splice(0)) waiter(frame);
        await this.onTurn?.(frame);
        break;
      default:
        this.replySettings(ws, frame);
        break;
    }
  }

  /**
   * The settings messages: models, providers, search, skill bodies and connectors.
   * Like the real host, a change answers with `done` alone (disable adds its
   * `connector.status`); only the list requests send lists.
   */
  private replySettings(ws: WebSocketRoute, frame: Frame): void {
    const done = () => this.emit({ re: frame.id, kind: "done", reason: "ok" }, ws);
    const fail = (code: string, message: string) => this.emit({ re: frame.id, kind: "error", code, message }, ws);
    const emitModels = () => this.emit({ re: frame.id, kind: "models", ...this.models }, ws);
    const emitSearch = () => this.emit({ re: frame.id, kind: "search.providers", ...this.search }, ws);
    const emitConnectors = () => this.emit({ re: frame.id, kind: "connectors", error: null, ...this.connectors }, ws);
    const name = String(frame.name ?? frame.provider ?? "");
    if (this.failing.has(String(frame.type))) return fail("internal", "That didn't work.");
    switch (frame.type) {
      case "models.list":
        emitModels();
        return done();
      case "set_model": {
        if (this.activeTurn !== null) return fail("busy", "Aldwin is still working on your last request.");
        const ref = String(frame.name);
        const known = this.models.providers.some((p) => p.models.some((m) => m.ref === ref));
        if (!known) return fail("unknown_model", `Unknown model ${ref}.`);
        this.models = { ...this.models, current: ref };
        this.settings.model = ref;
        return done();
      }
      case "provider.server":
        this.models.providers.push({
          name,
          title: String(frame.title ?? name),
          local: Boolean(frame.local),
          configured: true,
          status: "ok",
          models: [{ ref: `${name}/local-model`, id: "local-model", title: "Local model", context_window: null, supports_tools: true }],
        });
        return done();
      case "provider.disconnect":
        this.models.providers = this.models.providers.map((p) =>
          p.name === name ? { ...p, configured: false, status: "not_configured", models: [] } : p,
        );
        return done();
      case "search.list":
        emitSearch();
        return done();
      case "search.connect":
        this.search.items = this.search.items.map((i) =>
          i.name === name ? { ...i, connected: true, source: "file", detail: "key-…9999" } : i,
        );
        return done();
      case "search.use":
        this.search = {
          ...this.search,
          setting: name,
          active: name,
          items: this.search.items.map((i) => ({ ...i, active: i.name === name })),
        };
        return done();
      case "search.disconnect":
        this.search.items = this.search.items.map((i) =>
          i.name === name ? { ...i, connected: false, source: null, detail: "", active: false } : i,
        );
        return done();
      case "skills.show": {
        const body = this.skillBodies[name];
        if (body === undefined) return fail("not_found", `No skill called ${name}.`);
        this.emit({ re: frame.id, kind: "skill.body", name, body }, ws);
        return done();
      }
      case "connectors.list":
        emitConnectors();
        return done();
      case "connectors.add": {
        this.lastSignInId = frame.id;
        const item = this.connectors.catalog.find((c) => c.url === frame.url);
        const added = String(frame.name ?? item?.name ?? "custom");
        this.connectors.items.push({
          name: added,
          title: String(frame.title ?? item?.title ?? added),
          url: String(frame.url),
          enabled: true,
          status: "connected",
          auth: (frame.auth as "none" | "bearer" | "oauth") ?? "none",
          trust_read_only_hints: true,
          tools: [],
          error: null,
          retry_in_s: null,
          transport: "http",
          command: null,
          note: null,
        });
        this.connectors.catalog = this.connectors.catalog.map((c) => (c.name === added ? { ...c, added: true } : c));
        return done();
      }
      case "connectors.enable":
      case "connectors.disable": {
        const enabled = frame.type === "connectors.enable";
        const status: ConnectorStatus = enabled ? "connected" : "disabled";
        this.connectors.items = this.connectors.items.map((c) => (c.name === name ? { ...c, enabled, status } : c));
        // Disable answers with its status on the request; enable runs in the background and reports as the host.
        const re = enabled ? "host" : frame.id;
        this.emit({ re, kind: "connector.status", name, status, tools: 0, error: null, retry_in_s: null }, ws);
        return done();
      }
      case "connectors.remove":
        this.connectors.items = this.connectors.items.filter((c) => c.name !== name);
        return done();
      case "connectors.login":
        // The sign-in waits for the test to push `connector.auth` and then finish it.
        this.lastSignInId = frame.id;
        return;
      case "connectors.auth_paste":
      case "connectors.auth_token":
        return done();
      default:
        return;
    }
  }

  /** The host asks the user to sign a connector in, tied to the waiting login or add. */
  connectorAuth(name: string, mode: AuthMode, extra: { url?: string; expires_in?: number; token_help_url?: string } = {}): void {
    const title = this.connectors.items.find((c) => c.name === name)?.title ?? name;
    this.emit({
      re: this.lastSignInId ?? "host",
      kind: "connector.auth",
      name,
      title,
      mode,
      url: extra.url ?? null,
      token_help_url: extra.token_help_url ?? null,
      expires_in: extra.expires_in ?? 300,
    });
  }

  /** Finishes the waiting sign-in: the connector connects and the request ends. */
  finishSignIn(name: string): void {
    this.connectors.items = this.connectors.items.map((c) => (c.name === name ? { ...c, status: "connected" } : c));
    this.emit({ re: "host", kind: "connector.status", name, status: "connected", tools: 3, error: null, retry_in_s: null });
    if (this.lastSignInId) this.emit({ re: this.lastSignInId, kind: "done", reason: "ok" });
    this.lastSignInId = null;
  }

  /** Ends the waiting sign-in with an error, as when it runs out of time. */
  failSignIn(message: string): void {
    if (this.lastSignInId) this.emit({ re: this.lastSignInId, kind: "error", code: "auth_timeout", message });
    this.lastSignInId = null;
  }

  connectorStatus(name: string, status: ConnectorStatus): void {
    this.emit({ re: "host", kind: "connector.status", name, status, tools: 0, error: null, retry_in_s: null });
  }

  /** Per-conversation history, keyed by id in `histories`; the seeded history otherwise. */
  histories = new Map<string, HistoryRow[]>();
  private historyFor(row: SessionRow): HistoryRow[] {
    return this.histories.get(row.id) ?? [
      { role: "user", text: `About ${row.title}` },
      { role: "assistant", text: `Notes on ${row.title}.` },
    ];
  }

  private todoCounts() {
    return {
      items: this.todos,
      done: this.todos.filter((t) => t.status === "done").length,
      total: this.todos.length,
    };
  }

  /** Resolves with the next `turn` frame the page sends. */
  nextTurn(): Promise<Frame> {
    return new Promise((resolve) => this.turnWaiters.push(resolve));
  }

  private get turnId(): string {
    if (this.activeTurn === null) throw new Error("no turn is running");
    return this.activeTurn;
  }

  async stream(text: string, { chunkSize = 6, delayMs = 0 }: { chunkSize?: number; delayMs?: number } = {}): Promise<void> {
    const re = this.turnId;
    for (let i = 0; i < text.length; i += chunkSize) {
      this.emit({ re, kind: "text", delta: text.slice(i, i + chunkSize) });
      if (delayMs > 0) await new Promise((r) => setTimeout(r, delayMs));
    }
  }

  toolCall(event: Partial<{ call_id: string; name: string; args_preview: string; summary: string; connector: string; connector_title: string; remote_tool: string; reason: string }> = {}): void {
    this.emit({ re: this.turnId, kind: "tool_call", call_id: "c1", name: "read_file", args_preview: "", summary: "", ...event });
  }

  toolResult(
    event: Partial<{
      call_id: string;
      ok: boolean;
      summary: string;
      preview: string;
      duration_ms: number;
      display: Display | null;
    }> = {},
  ): void {
    this.emit({
      re: this.turnId,
      kind: "tool_result",
      call_id: "c1",
      ok: true,
      summary: "",
      preview: "",
      duration_ms: 0,
      ...event,
    });
  }

  emitToolResult(
    event: Partial<{
      call_id: string;
      ok: boolean;
      summary: string;
      preview: string;
      duration_ms: number;
      display: Display | null;
    }> = {},
  ): void {
    this.toolResult(event);
  }

  liveStatus(message: string, activeTool: string | null = null): void {
    this.emit({ re: this.turnId, kind: "status", message, turn_streamed_chunks: 0, active_tool: activeTool, partial_text: null });
  }

  todo(items: TodoItem[], re = this.activeTurn ?? "host"): void {
    this.todos = items;
    this.emit({ re, kind: "todo.updated", ...this.todoCounts() });
  }

  notice(message: string): void {
    this.emit({ re: this.activeTurn ?? "host", kind: "notice", message });
  }

  done(
    event: {
      reason?: string;
      usage?: { input_tokens: number; output_tokens: number } | null;
      max_steps?: number;
      context?: ContextUsage | null;
    } = {},
  ): void {
    const re = this.turnId;
    this.activeTurn = null;
    if (this.current && this.current.title === "") this.current.title = "Untitled";
    this.emit({ re, kind: "done", reason: "answered", ...event });
  }

  error(code: string, message: string): void {
    const re = this.turnId;
    this.activeTurn = null;
    this.emit({ re, kind: "error", code, message });
  }

  /** Answer the next turn with `text` and end it. */
  answerNext(text: string, tools: { name: string; ok: boolean }[] = []): void {
    this.onTurn = async (frame) => {
      tools.forEach((tool, i) => {
        this.emit({ re: frame.id, kind: "tool_call", call_id: `c${i}`, name: tool.name, args_preview: "", summary: "" });
        this.emit({ re: frame.id, kind: "tool_result", call_id: `c${i}`, ok: tool.ok, summary: "3 rows", preview: "", duration_ms: 120 });
      });
      this.emit({ re: frame.id, kind: "text", delta: text });
      this.activeTurn = null;
      this.emit({ re: frame.id, kind: "done", reason: "answered", usage: { input_tokens: 1234, output_tokens: 567 } });
    };
  }

  /** The connection drops as if the network went away. */
  async drop(code = 1006): Promise<void> {
    await this.socket.close({ code, reason: "" });
  }

  approval(
    event: Partial<{
      approval_id: string;
      call_id: string | null;
      tool: string;
      tier: "ask" | "diff";
      summary: string;
      detail: string;
      grantable: boolean;
      grant_scope: GrantScope | null;
      digest: string;
      risky: string[];
      connector: string | null;
      connector_title: string | null;
      remote_tool: string | null;
      display: Display | null;
    }> & Record<string, unknown> = {},
  ): void {
    this.emit({
      re: this.activeTurn ?? "host",
      kind: "approval",
      approval_id: "a1",
      call_id: "c1",
      tool: "write_file",
      tier: "ask",
      summary: "Write a file",
      detail: "",
      grantable: false,
      grant_scope: null,
      digest: "sha256:fixture",
      risky: [],
      ...event,
    });
  }

  emitApproval(
    event: Parameters<FakeHost["approval"]>[0] = {},
  ): void {
    this.approval(event);
  }

  // --- REST ---

  private async answerRest(route: Route): Promise<void> {
    const request = route.request();
    const { pathname } = new URL(request.url());
    const method = request.method();
    let body: unknown = null;
    try {
      body = request.postDataJSON();
    } catch {
      body = request.postData();
    }
    this.calls.push({ method, path: pathname, body });
    const custom = this.rest.get(`${method} ${pathname}`);
    if (custom) return custom(route, body);
    const reply = (json: unknown, status = 200) => {
      this.responses.push({ path: pathname, body: json });
      return route.fulfill({ status, json });
    };
    switch (`${method} ${pathname}`) {
      case "GET /v1/setup/status":
        return route.fulfill({ json: this.status });
      case "POST /v1/ws-ticket":
        if (this.ticketFailures.remaining > 0) {
          this.ticketFailures.remaining -= 1;
          return route.fulfill({
            status: this.ticketFailures.status,
            json: { detail: "Aldwin is not ready yet. Finish setup, then try again.", error: "host_not_ready" },
          });
        }
        return route.fulfill({ json: { ticket: `t-${this.sockets.length + 1}`, expires_in: 30 } });
      case "GET /v1/settings":
        return reply(this.settings);
      case "PATCH /v1/settings":
        return this.patchSettings(body as Record<string, unknown>, reply);
      case "POST /v1/settings/secrets":
        return this.saveSecret(body as { kind: string; name: string; key: string }, reply);
      case "POST /v1/setup/profile": {
        const update = body as Record<string, unknown>;
        if (update.skipped) this.status = { ...this.status, profile_done: true, profile_skipped: true };
        else if (update.complete) this.status = { ...this.status, profile_done: true };
        return route.fulfill({ json: this.status });
      }
      case "GET /v1/conversations":
        return reply({ items: this.sessions });
      default: {
        const convMatch = method === "GET" ? pathname.match(/^\/v1\/conversations\/([^/]+)$/) : null;
        if (convMatch) {
          const id = decodeURIComponent(convMatch[1]);
          const customConv = this.conversations.get(id);
          if (customConv) {
            if (customConv.delayMs && customConv.delayMs > 0) {
              await new Promise((r) => setTimeout(r, customConv.delayMs));
            }
            const status = customConv.status ?? 200;
            if (status >= 400) {
              return reply(customConv.body ?? { detail: NOT_FOUND }, status);
            }
            return reply(customConv.body ?? { meta: this.sessions.find((s) => s.id === id) ?? { id }, memory: "{}" }, status);
          }
          const found = this.sessions.find((s) => s.id === id) ?? (this.current?.id === id ? this.current : null);
          if (!found) return reply({ detail: NOT_FOUND }, 404);
          return reply({
            meta: found,
            memory: JSON.stringify({ version: 1, steps: [] }),
            turns: [],
          });
        }
        return route.fulfill({ status: 404, json: { detail: `No fake for ${method} ${pathname}` } });
      }
    }
  }

  /** Applies an allowed patch, or refuses all of it on the first bad field, as `apply_settings_patch` does. */
  private patchSettings(patch: Record<string, unknown>, reply: (json: unknown, status?: number) => Promise<void>) {
    const limits = (patch.limits ?? {}) as Record<string, unknown>;
    for (const [field, value] of Object.entries(limits)) {
      const minimum = LIMIT_MINIMUMS[field];
      if (minimum === undefined) return reply({ detail: `limits.${field}: Extra inputs are not permitted.` }, 400);
      if (typeof value !== "number" || !Number.isInteger(value)) return reply({ detail: `limits.${field}: Input should be a valid integer.` }, 400);
      if (value < minimum) return reply({ detail: `limits.${field}: Input should be greater than or equal to ${minimum}.` }, 400);
    }
    const unknown = Object.keys(patch).find((k) => !["model", "limits", "search", "check_for_updates"].includes(k));
    if (unknown) return reply({ detail: `${unknown} cannot be changed here.` }, 400);
    this.settings = {
      ...this.settings,
      limits: { ...this.settings.limits, ...(limits as Record<string, number>) },
      search: { ...this.settings.search, ...((patch.search ?? {}) as object) },
      ...(typeof patch.check_for_updates === "boolean" ? { check_for_updates: patch.check_for_updates } : {}),
    };
    return reply(this.settings);
  }

  /** Checks a key the way the host's vendor check answers, and saves only a good one. */
  private saveSecret(body: { kind: string; name: string; key: string }, reply: (json: unknown, status?: number) => Promise<void>) {
    const title = this.settings.keys.find((k) => k.name === body.name)?.title ?? body.name;
    if (body.key.startsWith("sk-bad")) return reply({ detail: `${title} refused this key.`, status: "invalid_key" }, 400);
    if (body.key.startsWith("sk-slow")) return reply({ detail: `${title} did not answer in time.`, status: "unreachable" }, 502);
    const masked = `sk-…${body.key.slice(-4)}`;
    this.settings.keys = this.settings.keys.map((k) => (k.name === body.name ? { ...k, source: "file", masked } : k));
    if (body.kind === "provider") {
      this.models.providers = this.models.providers.map((p) =>
        p.name === body.name ? { ...p, configured: true, status: "ok" } : p,
      );
    }
    return reply({ ok: true, kind: body.kind, name: body.name, masked, env_override: false });
  }

  calledPaths(): string[] {
    return this.calls.map((c) => `${c.method} ${c.path}`);
  }
}
