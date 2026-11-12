import type { Page, Route, WebSocketRoute } from "@playwright/test";

import type { SkillCatalogItem } from "../../src/types/protocol";

/** A frame the page sent over the socket. */
export type Frame = { id: string; type: string } & Record<string, unknown>;
export type HistoryRow = { role: "user" | "assistant" | "tool"; text: string };
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
      case "history":
        this.emit({ re: frame.id, kind: "history", messages: this.history, interrupted: this.interrupted }, ws);
        done();
        break;
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
      case "turn":
        // Like the host: one turn at a time.
        if (this.activeTurn !== null) {
          this.emit({ re: frame.id, kind: "error", code: "busy", message: TURN_BUSY }, ws);
          break;
        }
        this.activeTurn = frame.id;
        this.skillsUsed(frame, ws);
        if (this.current === null) {
          const row = session(`s${this.sessions.length + 1}`, String(frame.text), "2026-11-11T10:00:00Z");
          this.sessions = [row, ...this.sessions];
          this.current = row;
        }
        for (const waiter of this.turnWaiters.splice(0)) waiter(frame);
        await this.onTurn?.(frame);
        break;
      default:
        break;
    }
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

  toolResult(event: Partial<{ call_id: string; ok: boolean; summary: string; preview: string; duration_ms: number }> = {}): void {
    this.emit({ re: this.turnId, kind: "tool_result", call_id: "c1", ok: true, summary: "", preview: "", duration_ms: 0, ...event });
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

  done(event: { reason?: string; usage?: { input_tokens: number; output_tokens: number } | null; max_steps?: number } = {}): void {
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

  approval(event: Record<string, unknown>): void {
    this.emit({ re: this.activeTurn ?? "host", kind: "approval", approval_id: "a1", tool: "write_file", tier: "ask", summary: "Write a file", ...event });
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
        return route.fulfill({ json: { keys: [{ name: "openai", title: "OpenAI", kind: "provider" }, { name: "anthropic", title: "Anthropic", kind: "provider" }] } });
      case "POST /v1/setup/profile": {
        const update = body as Record<string, unknown>;
        if (update.skipped) this.status = { ...this.status, profile_done: true, profile_skipped: true };
        else if (update.complete) this.status = { ...this.status, profile_done: true };
        return route.fulfill({ json: this.status });
      }
      default:
        return route.fulfill({ status: 404, json: { detail: `No fake for ${method} ${pathname}` } });
    }
  }

  calledPaths(): string[] {
    return this.calls.map((c) => `${c.method} ${c.path}`);
  }
}
