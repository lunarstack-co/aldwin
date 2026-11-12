import { create } from "zustand";

import type {
  DoneReason,
  DroppedSkill,
  HistoryItem,
  SessionMeta,
  SkillCatalogItem,
  TodoItem,
  TokenUsage,
  ToolCallEvent,
  ToolResultEvent,
} from "@/types/protocol";

export type ToolRow = {
  callId: string;
  name: string;
  argsPreview: string;
  summary: string;
  preview: string;
  ok: boolean | null;
  durationMs: number;
  connectorTitle: string | null;
  remoteTool: string | null;
  reason: string | null;
};

export type TurnStatus = "streaming" | "stopping" | "answered" | "stopped" | "max_steps" | "error";

/** What the stage shows: the conversation, or every saved one with search. */
export type HistoryView = "chat" | "see-all";

/** Why the host refused to switch conversations; `message` is the host's own sentence. */
export type SessionNotice = { code: "busy" | "not_found" | "other"; message: string };

export type Turn = {
  id: string;
  userText: string;
  answer: string;
  tools: ToolRow[];
  status: TurnStatus;
  error: string | null;
  /** The host's error code, such as `provider_error`, shown beside the message. */
  errorCode: string | null;
  startedAt: number;
  endedAt: number | null;
  /** The host's latest status line and running tool while the turn is live. */
  statusMessage: string;
  activeTool: string | null;
  usage: TokenUsage | null;
  maxSteps: number | null;
  /** What the host made of the `@` skills on this turn; `null` when none were picked. */
  skillsUsed: { names: string[]; dropped: DroppedSkill[] } | null;
};

export type Todos = { items: TodoItem[]; done: number; total: number };

/** The `@` menu's catalogue: asked for the first time it opens, then kept up to date by the host. */
export type SkillsCatalog = { status: "idle" | "loading" | "ready" | "error"; items: SkillCatalogItem[] };

/** A passing message from the host, shown as a toast and never in the transcript. */
export type Notice = { id: number; message: string };

export type TurnEnd = { usage?: TokenUsage | null; maxSteps?: number | null };

type ChatState = {
  turns: Turn[];
  activeId: string | null;
  /** Saved conversations with at least one turn, newest first, and the open one. */
  sessions: SessionMeta[];
  currentSessionId: string | null;
  /** The host has listed its conversations at least once since this page loaded. */
  sessionsLoaded: boolean;
  /** The first history has arrived, so the transcript shows the real conversation. */
  historyLoaded: boolean;
  historyView: HistoryView;
  notice: SessionNotice | null;
  todos: Todos;
  toasts: Notice[];
  /** The host saved this conversation mid-answer; its last turn was cut off. */
  interrupted: boolean;
  /** Text to put back in the composer, after the host refused to start it. */
  draft: string | null;
  skills: SkillsCatalog;
  setTodos: (todos: Todos) => void;
  pushToast: (message: string) => void;
  dropToast: (id: number) => void;
  setTurnStatus: (message: string, activeTool: string | null) => void;
  setHistoryView: (view: HistoryView) => void;
  setNotice: (notice: SessionNotice | null) => void;
  setSessions: (items: SessionMeta[], current: SessionMeta | null) => void;
  startTurn: (id: string, text: string, now: number) => void;
  /** Take back a turn the host refused because another is running, keeping its text as a draft. */
  refuseTurn: (id: string) => void;
  clearDraft: () => void;
  setSkillsStatus: (status: SkillsCatalog["status"]) => void;
  setSkills: (items: SkillCatalogItem[]) => void;
  setSkillsUsed: (turnId: string, names: string[], dropped: DroppedSkill[]) => void;
  appendText: (delta: string) => void;
  addToolCall: (event: ToolCallEvent) => void;
  addToolResult: (event: ToolResultEvent) => void;
  markStopping: () => void;
  finishTurn: (reason: DoneReason, now: number, end?: TurnEnd) => void;
  failTurn: (message: string, now: number, code?: string) => void;
  /** Replace the transcript with the host's history, keeping a running turn at the end. */
  loadHistory: (items: HistoryItem[], interrupted?: boolean) => void;
  /** Make the host's in-flight turn the active one after a reconnect. */
  adoptRunningTurn: (
    partialText: string | null,
    activeTool: string | null,
    now: number,
    message?: string,
  ) => void;
  clearTranscript: () => void;
};

const DONE_STATUS: Record<DoneReason, TurnStatus> = {
  answered: "answered",
  stopped: "stopped",
  max_steps: "max_steps",
  ok: "answered",
};

function newTurn(id: string, userText: string, now: number, status: TurnStatus): Turn {
  return {
    id,
    userText,
    answer: "",
    tools: [],
    status,
    error: null,
    errorCode: null,
    startedAt: now,
    endedAt: null,
    statusMessage: "",
    activeTool: null,
    usage: null,
    maxSteps: null,
    skillsUsed: null,
  };
}

const NO_TODOS: Todos = { items: [], done: 0, total: 0 };
let toastSeq = 0;

function toolRow(callId: string, name: string): ToolRow {
  return {
    callId,
    name,
    argsPreview: "",
    summary: "",
    preview: "",
    ok: null,
    durationMs: 0,
    connectorTitle: null,
    remoteTool: null,
    reason: null,
  };
}

/** History rows arrive flat; each user row opens a turn that the rows after it fill. */
function groupHistory(items: HistoryItem[]): Turn[] {
  const turns: Turn[] = [];
  items.forEach((item, index) => {
    if (item.role === "user" || turns.length === 0) {
      turns.push(newTurn(`history_${index}`, item.role === "user" ? item.text : "", 0, "answered"));
    }
    const turn = turns[turns.length - 1];
    if (item.role === "assistant") {
      turn.answer = turn.answer ? `${turn.answer}\n\n${item.text}` : item.text;
    } else if (item.role === "tool") {
      const failed = item.text.startsWith("✗");
      turn.tools.push({ ...toolRow(`history_${index}`, item.text.replace(/^[✓✗]\s*/, "")), ok: !failed });
    }
  });
  return turns;
}

function updateActive(state: ChatState, change: (turn: Turn) => Turn): Partial<ChatState> {
  if (state.activeId === null) return {};
  return { turns: state.turns.map((t) => (t.id === state.activeId ? change(t) : t)) };
}

export const useChatStore = create<ChatState>((set, get) => ({
  turns: [],
  activeId: null,
  sessions: [],
  currentSessionId: null,
  sessionsLoaded: false,
  historyLoaded: false,
  historyView: "chat",
  notice: null,
  todos: NO_TODOS,
  toasts: [],
  interrupted: false,
  draft: null,
  skills: { status: "idle", items: [] },

  setHistoryView: (view) => set({ historyView: view }),
  setNotice: (notice) => set({ notice }),
  setTodos: (todos) => set({ todos }),
  pushToast: (message) => set((state) => ({ toasts: [...state.toasts, { id: ++toastSeq, message }] })),
  dropToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
  setTurnStatus: (message, activeTool) =>
    set((state) => updateActive(state, (t) => ({ ...t, statusMessage: message, activeTool }))),

  setSessions: (items, current) =>
    set({ sessions: items, currentSessionId: current?.id ?? null, sessionsLoaded: true }),

  startTurn: (id, text, now) =>
    set((state) => ({
      turns: [...state.turns, newTurn(id, text, now, "streaming")],
      activeId: id,
      notice: null,
      interrupted: false,
    })),

  refuseTurn: (id) =>
    set((state) => ({
      turns: state.turns.filter((t) => t.id !== id),
      activeId: state.activeId === id ? null : state.activeId,
      draft: state.turns.find((t) => t.id === id)?.userText ?? null,
    })),

  clearDraft: () => set({ draft: null }),

  setSkillsStatus: (status) => set((state) => ({ skills: { ...state.skills, status } })),
  setSkills: (items) => set({ skills: { status: "ready", items } }),
  setSkillsUsed: (turnId, names, dropped) =>
    set((state) => ({
      turns: state.turns.map((t) => (t.id === turnId ? { ...t, skillsUsed: { names, dropped } } : t)),
    })),

  appendText: (delta) =>
    set((state) => updateActive(state, (t) => ({ ...t, answer: t.answer + delta }))),

  addToolCall: (event) =>
    set((state) =>
      updateActive(state, (t) => ({
        ...t,
        tools: [
          ...t.tools,
          {
            ...toolRow(event.call_id, event.name),
            argsPreview: event.args_preview,
            summary: event.summary,
            connectorTitle: event.connector ? (event.connector_title ?? event.connector) : null,
            remoteTool: event.remote_tool ?? null,
            reason: event.reason ?? null,
          },
        ],
      })),
    ),

  addToolResult: (event) =>
    set((state) =>
      updateActive(state, (t) => ({
        ...t,
        tools: t.tools.map((row) =>
          row.callId === event.call_id
            ? {
                ...row,
                ok: event.ok,
                summary: event.summary || row.summary,
                preview: event.preview,
                durationMs: event.duration_ms,
              }
            : row,
        ),
      })),
    ),

  markStopping: () => set((state) => updateActive(state, (t) => ({ ...t, status: "stopping" }))),

  finishTurn: (reason, now, end = {}) =>
    set((state) => ({
      ...updateActive(state, (t) => ({
        ...t,
        status: DONE_STATUS[reason],
        endedAt: now,
        activeTool: null,
        usage: end.usage ?? null,
        maxSteps: end.maxSteps ?? null,
      })),
      activeId: null,
      notice: null,
    })),

  failTurn: (message, now, code) =>
    set((state) => ({
      ...updateActive(state, (t) => ({ ...t, status: "error", error: message, errorCode: code ?? null, endedAt: now })),
      activeId: null,
      notice: null,
    })),

  loadHistory: (items, interrupted = false) => {
    const { turns, activeId } = get();
    const fromHistory = groupHistory(items);
    const active = turns.find((t) => t.id === activeId);
    if (active) {
      const last = fromHistory[fromHistory.length - 1];
      // The in-flight turn's own user row is already in history; fold it in.
      if (last && last.answer === "" && !active.userText) {
        fromHistory.pop();
        active.userText = last.userText;
        active.tools = [...last.tools, ...active.tools];
      }
      fromHistory.push(active);
    }
    set({ turns: fromHistory, interrupted: interrupted && active === undefined, historyLoaded: true });
  },

  adoptRunningTurn: (partialText, activeTool, now, message = "") =>
    set((state) => {
      const id = `adopted_${now}`;
      const turn = newTurn(id, "", now, "streaming");
      turn.answer = partialText ?? "";
      turn.statusMessage = message;
      turn.activeTool = activeTool;
      if (activeTool) turn.tools.push(toolRow(`${id}_tool`, activeTool));
      return { turns: [...state.turns, turn], activeId: id };
    }),

  clearTranscript: () => set({ turns: [], activeId: null, notice: null, todos: NO_TODOS, interrupted: false }),
}));

/** The text Retry sends again as a new turn: the last thing the user asked. */
export function lastUserText(turns: Turn[]): string | null {
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (turns[i].userText) return turns[i].userText;
  }
  return null;
}
