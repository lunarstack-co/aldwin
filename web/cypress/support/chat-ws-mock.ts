// A stand-in for the browser's WebSocket so the chat shell can be driven
// without a running host. Each spec scripts the host's side: what history
// to answer with, what a turn streams, and when the socket drops.

export type Frame = { id: string; type: string } & Record<string, unknown>;
export type HostEvent = { re: string; kind: string } & Record<string, unknown>;
export type HistoryRow = { role: "user" | "assistant" | "tool"; text: string };
export type SessionRow = {
  id: string;
  created_at: string;
  updated_at: string;
  model: string;
  title: string;
  interrupted: boolean;
};
export type StatusReply = {
  turn_streamed_chunks: number;
  active_tool: string | null;
  partial_text: string | null;
};
export type Refusal = { code: number; reason: string };
/** What `sessions.list` answers with: zero-turn conversations never appear in `items`. */
export type SessionSeed = { items: SessionRow[]; current: SessionRow | null };

const BUSY = "Aldwin is still working on your last request. Stop it before switching conversations.";

const CLOSED = 3;
const OPEN = 1;

export class MockSocket {
  static instances: MockSocket[] = [];
  static history: HistoryRow[] = [];
  /** Saved conversations, newest first; a turn on a fresh conversation adds one. */
  static sessions: SessionRow[] = [];
  static currentSession: SessionRow | null = null;
  static status: StatusReply = { turn_streamed_chunks: 0, active_tool: null, partial_text: null };
  /** When set, every new socket is closed with this code instead of opening. */
  static refuseWith: Refusal | null = null;
  /** When set, the next `sessions.open` fails with this error instead of switching. */
  static openError: { code: string; message: string } | null = null;
  /** Runs when a turn arrives, so a spec can stream an answer. */
  static onTurn: ((socket: MockSocket, frame: Frame) => void) | null = null;

  static reset(): void {
    MockSocket.instances = [];
    MockSocket.history = [];
    MockSocket.sessions = [];
    MockSocket.currentSession = null;
    MockSocket.status = { turn_streamed_chunks: 0, active_tool: null, partial_text: null };
    MockSocket.refuseWith = null;
    MockSocket.openError = null;
    MockSocket.onTurn = null;
  }

  /** Seed the saved conversations and the open one, the way the host lists them. */
  static seed(seed: SessionSeed): void {
    MockSocket.sessions = seed.items;
    MockSocket.currentSession = seed.current;
  }

  static last(): MockSocket {
    const socket = MockSocket.instances[MockSocket.instances.length - 1];
    if (!socket) throw new Error("no socket has been opened yet");
    return socket;
  }

  readonly url: string;
  readyState = 0;
  sent: Frame[] = [];
  activeTurn: string | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  onclose: ((closed: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockSocket.instances.push(this);
    // Microtasks are not under cy.clock, so the socket opens even with fake timers.
    void Promise.resolve().then(() => {
      const refusal = MockSocket.refuseWith;
      if (refusal) {
        this.drop(refusal.code, refusal.reason);
      } else {
        this.readyState = OPEN;
        this.onopen?.();
      }
    });
  }

  /** The page's side of the socket. */
  send(data: string): void {
    const frame = JSON.parse(data) as Frame;
    this.sent.push(frame);
    this.reply(frame);
  }

  close(): void {
    this.readyState = CLOSED;
  }

  /** The host's side: push one event to the page. */
  emit(event: HostEvent): void {
    if (this.readyState !== OPEN) return;
    const payload = { v: 2, type: "event", channel: "web", sender: "host", ...event };
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  /** The host's side: the connection ends with `code`. */
  drop(code: number, reason = ""): void {
    if (this.readyState === CLOSED) return;
    this.readyState = CLOSED;
    this.onclose?.({ code, reason });
  }

  sentOfType(type: string): Frame[] {
    return this.sent.filter((frame) => frame.type === type);
  }

  private refuseWhileBusy(frame: Frame): boolean {
    if (this.activeTurn === null) return false;
    this.emit({ re: frame.id, kind: "error", code: "busy", message: BUSY });
    return true;
  }

  private reply(frame: Frame): void {
    switch (frame.type) {
      case "hello":
        this.emit({ re: frame.id, kind: "done", reason: "ok" });
        break;
      case "sessions.new":
        if (this.refuseWhileBusy(frame)) break;
        // The host's landing rule: an empty conversation, never listed until it has a turn.
        MockSocket.currentSession = null;
        this.emit({ re: frame.id, kind: "sessions", items: [], current: null });
        this.emit({ re: frame.id, kind: "done", reason: "ok" });
        break;
      case "sessions.list":
        this.emit({
          re: frame.id,
          kind: "sessions",
          items: MockSocket.sessions,
          current: MockSocket.currentSession,
        });
        this.emit({ re: frame.id, kind: "done", reason: "ok" });
        break;
      case "sessions.open": {
        if (this.refuseWhileBusy(frame)) break;
        if (MockSocket.openError) {
          this.emit({ re: frame.id, kind: "error", ...MockSocket.openError });
          MockSocket.openError = null;
          break;
        }
        const found = MockSocket.sessions.find((row) => row.id === frame.session_id);
        if (found) {
          MockSocket.currentSession = found;
          this.emit({ re: frame.id, kind: "sessions", items: [], current: found });
          this.emit({ re: frame.id, kind: "done", reason: "ok" });
        } else {
          this.emit({ re: frame.id, kind: "error", code: "not_found", message: "That conversation no longer exists." });
        }
        break;
      }
      case "history":
        this.emit({ re: frame.id, kind: "history", messages: MockSocket.history, interrupted: false });
        this.emit({ re: frame.id, kind: "done", reason: "ok" });
        break;
      case "status":
        this.emit({ re: frame.id, kind: "status", message: "", ...MockSocket.status });
        this.emit({ re: frame.id, kind: "done", reason: "ok" });
        break;
      case "stop":
        this.emit({ re: frame.id, kind: "done", reason: "ok" });
        if (this.activeTurn !== null) {
          this.emit({ re: this.activeTurn, kind: "done", reason: "stopped" });
          this.activeTurn = null;
        }
        break;
      case "turn":
        this.activeTurn = frame.id;
        if (MockSocket.currentSession === null) {
          const row: SessionRow = {
            id: `s${MockSocket.sessions.length + 1}`,
            created_at: "2026-11-11T10:00:00Z",
            updated_at: "2026-11-11T10:00:00Z",
            model: "openai/gpt-4o",
            title: String(frame.text),
            interrupted: false,
          };
          MockSocket.sessions = [row, ...MockSocket.sessions];
          MockSocket.currentSession = row;
        }
        MockSocket.onTurn?.(this, frame);
        break;
      default:
        break;
    }
  }
}

/** Stream `chunks` as text events for `turnId`, then end the turn. */
export function streamAnswer(
  socket: MockSocket,
  turnId: string,
  chunks: string[],
  reason: "answered" | "max_steps" = "answered",
): void {
  for (const delta of chunks) socket.emit({ re: turnId, kind: "text", delta });
  socket.emit({ re: turnId, kind: "done", reason });
  socket.activeTurn = null;
}
