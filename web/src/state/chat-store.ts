import { create } from "zustand";

import type {
  DoneReason,
  HistoryItem,
  SessionMeta,
  ToolCallEvent,
  ToolResultEvent,
} from "@/types/protocol";

export type ToolRow = {
  callId: string;
  name: string;
  argsPreview: string;
  summary: string;
  ok: boolean | null;
  durationMs: number;
};

export type TurnStatus = "streaming" | "stopping" | "answered" | "stopped" | "max_steps" | "error";

export type Turn = {
  id: string;
  userText: string;
  answer: string;
  tools: ToolRow[];
  status: TurnStatus;
  error: string | null;
  startedAt: number;
  endedAt: number | null;
};

type ChatState = {
  turns: Turn[];
  activeId: string | null;
  /** Saved conversations with at least one turn, newest first, and the open one. */
  sessions: SessionMeta[];
  currentSessionId: string | null;
  setSessions: (items: SessionMeta[], current: SessionMeta | null) => void;
  startTurn: (id: string, text: string, now: number) => void;
  appendText: (delta: string) => void;
  addToolCall: (event: ToolCallEvent) => void;
  addToolResult: (event: ToolResultEvent) => void;
  markStopping: () => void;
  finishTurn: (reason: DoneReason, now: number) => void;
  failTurn: (message: string, now: number) => void;
  /** Replace the transcript with the host's history, keeping a running turn at the end. */
  loadHistory: (items: HistoryItem[]) => void;
  /** Make the host's in-flight turn the active one after a reconnect. */
  adoptRunningTurn: (partialText: string | null, activeTool: string | null, now: number) => void;
  clearTranscript: () => void;
};

const DONE_STATUS: Record<DoneReason, TurnStatus> = {
  answered: "answered",
  stopped: "stopped",
  max_steps: "max_steps",
  ok: "answered",
};

function newTurn(id: string, userText: string, now: number, status: TurnStatus): Turn {
  return { id, userText, answer: "", tools: [], status, error: null, startedAt: now, endedAt: null };
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
      turn.tools.push({
        callId: `history_${index}`,
        name: item.text.replace(/^[✓✗]\s*/, ""),
        argsPreview: "",
        summary: "",
        ok: !failed,
        durationMs: 0,
      });
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

  setSessions: (items, current) => set({ sessions: items, currentSessionId: current?.id ?? null }),

  startTurn: (id, text, now) =>
    set((state) => ({ turns: [...state.turns, newTurn(id, text, now, "streaming")], activeId: id })),

  appendText: (delta) =>
    set((state) => updateActive(state, (t) => ({ ...t, answer: t.answer + delta }))),

  addToolCall: (event) =>
    set((state) =>
      updateActive(state, (t) => ({
        ...t,
        tools: [
          ...t.tools,
          {
            callId: event.call_id,
            name: event.name,
            argsPreview: event.args_preview,
            summary: event.summary,
            ok: null,
            durationMs: 0,
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
            ? { ...row, ok: event.ok, summary: event.summary || row.summary, durationMs: event.duration_ms }
            : row,
        ),
      })),
    ),

  markStopping: () => set((state) => updateActive(state, (t) => ({ ...t, status: "stopping" }))),

  finishTurn: (reason, now) =>
    set((state) => ({
      ...updateActive(state, (t) => ({ ...t, status: DONE_STATUS[reason], endedAt: now })),
      activeId: null,
    })),

  failTurn: (message, now) =>
    set((state) => ({
      ...updateActive(state, (t) => ({ ...t, status: "error", error: message, endedAt: now })),
      activeId: null,
    })),

  loadHistory: (items) => {
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
    set({ turns: fromHistory });
  },

  adoptRunningTurn: (partialText, activeTool, now) =>
    set((state) => {
      const id = `adopted_${now}`;
      const turn = newTurn(id, "", now, "streaming");
      turn.answer = partialText ?? "";
      if (activeTool) {
        turn.tools.push({
          callId: `${id}_tool`,
          name: activeTool,
          argsPreview: "",
          summary: "",
          ok: null,
          durationMs: 0,
        });
      }
      return { turns: [...state.turns, turn], activeId: id };
    }),

  clearTranscript: () => set({ turns: [], activeId: null }),
}));

/** The text Retry sends again as a new turn: the last thing the user asked. */
export function lastUserText(turns: Turn[]): string | null {
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (turns[i].userText) return turns[i].userText;
  }
  return null;
}
