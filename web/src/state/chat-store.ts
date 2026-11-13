import { create } from "zustand";

import type {
  ApprovalAnswer,
  ApprovalEvent,
  ContextUsage,
  Display,
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

export type ApprovalState = "request" | "running" | "done" | "denied";

export type TurnApproval = {
  event: ApprovalEvent;
  state: ApprovalState;
  answer?: ApprovalAnswer;
  reason?: string | null;
};

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
  display: Display | null;
  /** How much of the answer had streamed when this call started, so it is drawn after that text. */
  textBefore: number;
};

export type TurnStatus = "streaming" | "stopping" | "answered" | "stopped" | "max_steps" | "error";

export type HistoryView = "chat" | "see-all";

/** `message` is the host's own sentence. */
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
  firstTokenAt: number | null;
  endedAt: number | null;
  userAt: string | null;
  assistantAt: string | null;
  statusMessage: string;
  activeTool: string | null;
  usage: TokenUsage | null;
  maxSteps: number | null;
  context: ContextUsage | null;
  approval: TurnApproval | null;
  approvalsByCallId: Record<string, TurnApproval>;
  discarded: boolean;
  /** A tool ran after some text, so the next text starts a new paragraph. */
  breakBeforeText: boolean;
  /** What the host made of the `@` skills on this turn; `null` when none were picked. */
  skillsUsed: { names: string[]; dropped: DroppedSkill[] } | null;
  pickedApps: PickedApp[];
  appsUsed: { names: string[]; dropped: DroppedSkill[] } | null;
};

/** `name` is the connector name the host knows. */
export type PickedApp = { name: string; title: string };

export type Todos = { items: TodoItem[]; done: number; total: number };

/** The `@` menu's catalogue: asked for the first time it opens, then kept up to date by the host. */
export type SkillsCatalog = { status: "idle" | "loading" | "ready" | "error"; items: SkillCatalogItem[] };

/** Shown as a toast, never in the transcript. */
export type Notice = { id: number; message: string };

export type TurnEnd = {
  usage?: TokenUsage | null;
  maxSteps?: number | null;
  context?: ContextUsage | null;
};

type ChatState = {
  turns: Turn[];
  activeId: string | null;
  /** Saved conversations with at least one turn, newest first, and the open one. */
  sessions: SessionMeta[];
  currentSessionId: string | null;
  /** The host has listed its conversations at least once since this page loaded. */
  sessionsLoaded: boolean;
  /** Deleted in this tab, so an address that still names one goes to a new chat instead. */
  deletedSessionIds: string[];
  /** The first history has arrived, so the transcript shows the real conversation. */
  historyLoaded: boolean;
  historyView: HistoryView;
  notice: SessionNotice | null;
  todos: Todos;
  /** The turn that last changed the plan, so a finished plan from earlier work stays away. */
  todosTurnId: string | null;
  toasts: Notice[];
  /** The host saved this conversation mid-answer; its last turn was cut off. */
  interrupted: boolean;
  /** Text to put back in the composer, after the host refused to start it. */
  draft: string | null;
  skills: SkillsCatalog;
  context: ContextUsage | null;
  waitingApproval: string | null;
  setWaitingApproval: (summary: string | null) => void;
  setApproval: (event: ApprovalEvent) => void;
  answerApproval: (answer: ApprovalAnswer, reason?: string | null) => void;
  settleApproval: (callId?: string | null) => void;
  clearApproval: (approvalId?: string) => void;
  discardTurn: (turnId: string) => void;
  /** Apps picked for the message being written; they go with the next send. */
  pickedApps: PickedApp[];
  pickApp: (app: PickedApp) => void;
  unpickApp: (name: string) => void;
  setAppsUsed: (turnId: string, names: string[], dropped: DroppedSkill[]) => void;
  setTodos: (todos: Todos) => void;
  pushToast: (message: string) => void;
  dropToast: (id: number) => void;
  setTurnStatus: (message: string, activeTool: string | null) => void;
  setHistoryView: (view: HistoryView) => void;
  setNotice: (notice: SessionNotice | null) => void;
  setSessions: (items: SessionMeta[], current: SessionMeta | null) => void;
  forgetSession: (id: string) => void;
  startTurn: (id: string, text: string, now: number, apps?: PickedApp[]) => void;
  /** Take back a turn the host refused because another is running, keeping its text as a draft. */
  refuseTurn: (id: string) => void;
  clearDraft: () => void;
  setSkillsStatus: (status: SkillsCatalog["status"]) => void;
  setSkills: (items: SkillCatalogItem[]) => void;
  setSkillsUsed: (turnId: string, names: string[], dropped: DroppedSkill[]) => void;
  appendText: (delta: string, now?: number) => void;
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

/** Text written after a tool call reads as its own paragraph, not glued to the last sentence. */
export function joinParagraph(answer: string, next: string): string {
  if (answer.trim() === "") return answer + next;
  const head = answer.replace(/\s+$/, "");
  return `${head}\n\n${next.replace(/^\s+/, "")}`;
}

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
    firstTokenAt: null,
    endedAt: null,
    userAt: now > 0 ? new Date(now).toISOString() : null,
    assistantAt: null,
    statusMessage: "",
    activeTool: null,
    usage: null,
    maxSteps: null,
    context: null,
    approval: null,
    approvalsByCallId: {},
    discarded: false,
    breakBeforeText: false,
    skillsUsed: null,
    pickedApps: [],
    appsUsed: null,
  };
}

const NO_TODOS: Todos = { items: [], done: 0, total: 0 };
let toastSeq = 0;

/** Where a tool call falls in the answer: after the text so far, less any trailing space a paragraph break drops. */
function textEnd(answer: string): number {
  return answer.replace(/\s+$/, "").length;
}

function toolRow(callId: string, name: string, textBefore = 0): ToolRow {
  return {
    textBefore,
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
    display: null,
  };
}

/** History rows arrive flat; each user row opens a turn that the rows after it fill. */
function groupHistory(items: HistoryItem[]): Turn[] {
  const turns: Turn[] = [];
  items.forEach((item, index) => {
    if (item.role === "user" || turns.length === 0) {
      const turn = newTurn(`history_${index}`, item.role === "user" ? item.text : "", 0, "answered");
      if (item.role === "user") {
        turn.userAt = item.at ?? null;
        turn.pickedApps = (item.apps ?? []).map(({ name, title }) => ({ name, title }));
      }
      turns.push(turn);
    }
    const turn = turns[turns.length - 1];
    if (item.role === "assistant") {
      turn.answer = turn.answer ? `${turn.answer}\n\n${item.text}` : item.text;
      if (item.at) turn.assistantAt = item.at;
    } else if (item.role === "tool") {
      const failed = item.text.startsWith("✗");
      turn.tools.push({
        ...toolRow(`history_${index}`, item.text.replace(/^[✓✗]\s*/, ""), textEnd(turn.answer)),
        ok: !failed,
      });
    }
  });
  return turns;
}

function updateActive(state: ChatState, change: (turn: Turn) => Turn): Partial<ChatState> {
  if (state.activeId === null) return {};
  return { turns: state.turns.map((t) => (t.id === state.activeId ? change(t) : t)) };
}

function dropUnsettledApprovals(map: Record<string, TurnApproval>): Record<string, TurnApproval> {
  const kept: Record<string, TurnApproval> = {};
  for (const [key, entry] of Object.entries(map)) {
    if (entry.state === "done" || entry.state === "denied") kept[key] = entry;
  }
  return kept;
}

export const useChatStore = create<ChatState>((set, get) => ({
  turns: [],
  activeId: null,
  sessions: [],
  currentSessionId: null,
  sessionsLoaded: false,
  deletedSessionIds: [],
  historyLoaded: false,
  historyView: "chat",
  notice: null,
  todos: NO_TODOS,
  todosTurnId: null,
  toasts: [],
  interrupted: false,
  draft: null,
  skills: { status: "idle", items: [] },
  context: null,
  pickedApps: [],
  waitingApproval: null,
  setWaitingApproval: (summary) => set({ waitingApproval: summary }),

  setApproval: (event) =>
    set((state) => ({
      waitingApproval: event.summary,
      ...updateActive(state, (t) => {
        const entry: TurnApproval = { event, state: "request" };
        const byCall = event.call_id
          ? { ...t.approvalsByCallId, [event.call_id]: entry }
          : t.approvalsByCallId;
        return { ...t, approval: entry, approvalsByCallId: byCall };
      }),
    })),

  answerApproval: (answer, reason = null) =>
    set((state) => ({
      waitingApproval: null,
      ...updateActive(state, (t) => {
        if (!t.approval || t.approval.state !== "request") return t;
        const nextState: ApprovalState = answer === "no" ? "denied" : "running";
        const updated: TurnApproval = {
          ...t.approval,
          state: nextState,
          answer,
          reason: answer === "no" ? (reason ?? null) : null,
        };
        const callId = updated.event.call_id;
        return {
          ...t,
          approval: updated,
          approvalsByCallId: callId
            ? { ...t.approvalsByCallId, [callId]: updated }
            : t.approvalsByCallId,
        };
      }),
    })),

  settleApproval: (callId = null) =>
    set((state) =>
      updateActive(state, (t) => {
        const targetId = callId ?? t.approval?.event.call_id ?? null;
        let nextApproval = t.approval;
        if (
          nextApproval &&
          nextApproval.state === "running" &&
          (!targetId || nextApproval.event.call_id === targetId || !nextApproval.event.call_id)
        ) {
          nextApproval = { ...nextApproval, state: "done" };
        }
        let nextMap = t.approvalsByCallId;
        if (targetId && nextMap[targetId]?.state === "running") {
          nextMap = { ...nextMap, [targetId]: { ...nextMap[targetId], state: "done" } };
        }
        return { ...t, approval: nextApproval, approvalsByCallId: nextMap };
      }),
    ),

  clearApproval: (approvalId) =>
    set((state) => ({
      waitingApproval: null,
      ...updateActive(state, (t) => {
        const shouldClearActive =
          t.approval !== null && (!approvalId || t.approval.event.approval_id === approvalId);
        const nextMap: Record<string, TurnApproval> = {};
        for (const [k, entry] of Object.entries(t.approvalsByCallId)) {
          const matches = !approvalId || entry.event.approval_id === approvalId;
          if (matches && (entry.state === "request" || entry.state === "running")) continue;
          nextMap[k] = entry;
        }
        return {
          ...t,
          approval: shouldClearActive ? null : t.approval,
          approvalsByCallId: nextMap,
        };
      }),
    })),

  discardTurn: (turnId) =>
    set((state) => ({
      turns: state.turns.map((t) => (t.id === turnId ? { ...t, discarded: true } : t)),
    })),

  setHistoryView: (view) => set({ historyView: view }),
  setNotice: (notice) => set({ notice }),
  setTodos: (todos) => set((state) => ({ todos, todosTurnId: state.activeId })),
  pushToast: (message) => set((state) => ({ toasts: [...state.toasts, { id: ++toastSeq, message }] })),
  dropToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),
  setTurnStatus: (message, activeTool) =>
    set((state) => updateActive(state, (t) => ({ ...t, statusMessage: message, activeTool }))),

  forgetSession: (id) => {
    if (get().currentSessionId === id) get().clearTranscript();
    set((state) => ({ deletedSessionIds: [...state.deletedSessionIds, id] }));
  },
  setSessions: (items, current) =>
    set({ sessions: items, currentSessionId: current?.id ?? null, sessionsLoaded: true }),

  startTurn: (id, text, now, apps = []) =>
    set((state) => ({
      turns: [...state.turns, { ...newTurn(id, text, now, "streaming"), pickedApps: apps }],
      pickedApps: [],
      activeId: id,
      notice: null,
      interrupted: false,
      waitingApproval: null,
    })),

  refuseTurn: (id) =>
    set((state) => {
      const refused = state.turns.find((t) => t.id === id);
      return {
        turns: state.turns.filter((t) => t.id !== id),
        activeId: state.activeId === id ? null : state.activeId,
        draft: refused?.userText ?? null,
        pickedApps: refused?.pickedApps ?? state.pickedApps,
      };
    }),

  clearDraft: () => set({ draft: null }),

  setSkillsStatus: (status) => set((state) => ({ skills: { ...state.skills, status } })),
  setSkills: (items) => set({ skills: { status: "ready", items } }),
  pickApp: (app) =>
    set((state) =>
      state.pickedApps.some((a) => a.name === app.name) ? state : { pickedApps: [...state.pickedApps, app] },
    ),
  unpickApp: (name) => set((state) => ({ pickedApps: state.pickedApps.filter((a) => a.name !== name) })),
  setAppsUsed: (turnId, names, dropped) =>
    set((state) => ({
      turns: state.turns.map((t) => (t.id === turnId ? { ...t, appsUsed: { names, dropped } } : t)),
    })),

  setSkillsUsed: (turnId, names, dropped) =>
    set((state) => ({
      turns: state.turns.map((t) => (t.id === turnId ? { ...t, skillsUsed: { names, dropped } } : t)),
    })),

  appendText: (delta, now = Date.now()) =>
    set((state) =>
      updateActive(state, (t) => {
        const startsParagraph = t.breakBeforeText && delta.trim() !== "";
        return {
          ...t,
          answer: startsParagraph ? joinParagraph(t.answer, delta) : t.answer + delta,
          breakBeforeText: startsParagraph ? false : t.breakBeforeText,
          firstTokenAt: t.firstTokenAt === null && delta.length > 0 ? now : t.firstTokenAt,
        };
      }),
    ),

  addToolCall: (event) =>
    set((state) =>
      updateActive(state, (t) => ({
        ...t,
        breakBeforeText: t.answer.trim() !== "",
        tools: [
          ...t.tools,
          {
            ...toolRow(event.call_id, event.name, textEnd(t.answer)),
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
      updateActive(state, (t) => {
        let nextApproval = t.approval;
        if (
          nextApproval &&
          nextApproval.state === "running" &&
          (!nextApproval.event.call_id || nextApproval.event.call_id === event.call_id)
        ) {
          nextApproval = { ...nextApproval, state: "done" };
        }
        let nextMap = t.approvalsByCallId;
        if (nextMap[event.call_id]?.state === "running") {
          nextMap = {
            ...nextMap,
            [event.call_id]: { ...nextMap[event.call_id], state: "done" },
          };
        }
        return {
          ...t,
          approval: nextApproval,
          approvalsByCallId: nextMap,
          tools: t.tools.map((row) =>
            row.callId === event.call_id
              ? {
                  ...row,
                  ok: event.ok,
                  summary: event.summary || row.summary,
                  preview: event.preview,
                  durationMs: event.duration_ms,
                  display: event.display ?? null,
                }
              : row,
          ),
        };
      }),
    ),

  markStopping: () => set((state) => updateActive(state, (t) => ({ ...t, status: "stopping" }))),

  finishTurn: (reason, now, end = {}) =>
    set((state) => ({
      ...updateActive(state, (t) => ({
        ...t,
        status: DONE_STATUS[reason],
        endedAt: now,
        assistantAt: now > 0 ? new Date(now).toISOString() : t.assistantAt,
        activeTool: null,
        usage: end.usage ?? null,
        maxSteps: end.maxSteps ?? null,
        context: end.context ?? null,
        approval: null,
        approvalsByCallId: dropUnsettledApprovals(t.approvalsByCallId),
      })),
      activeId: null,
      notice: null,
      waitingApproval: null,
      context: end.context !== undefined ? (end.context ?? null) : state.context,
    })),

  failTurn: (message, now, code) =>
    set((state) => ({
      ...updateActive(state, (t) => ({
        ...t,
        status: "error",
        error: message,
        errorCode: code ?? null,
        endedAt: now,
        approval: null,
        approvalsByCallId: dropUnsettledApprovals(t.approvalsByCallId),
      })),
      activeId: null,
      notice: null,
      waitingApproval: null,
    })),

  loadHistory: (items, interrupted = false) => {
    const { turns, activeId } = get();
    const fromHistory = groupHistory(items);
    const active = turns.find((t) => t.id === activeId);
    if (active) {
      const copyActive: Turn = { ...active, tools: [...active.tools] };
      const last = fromHistory[fromHistory.length - 1];
      // Fold the host's saved in-flight user row into the live turn so reconnect never duplicates it.
      if (last && last.answer === "" && (!copyActive.userText || last.userText === copyActive.userText)) {
        fromHistory.pop();
        if (!copyActive.userText) {
          copyActive.userText = last.userText;
          copyActive.userAt = last.userAt ?? copyActive.userAt;
        }
        if (copyActive.tools.length === 0 && last.tools.length > 0) {
          copyActive.tools = [...last.tools];
        }
      }
      fromHistory.push(copyActive);
    }
    set({
      turns: fromHistory,
      interrupted: interrupted && active === undefined,
      historyLoaded: true,
      context: active ? get().context : null,
    });
  },

  adoptRunningTurn: (partialText, activeTool, now, message = "") =>
    set((state) => {
      if (state.activeId !== null) {
        return updateActive(state, (t) => ({
          ...t,
          // Tool positions are counted in this tab's text, so only an answer with no tools yet is swapped.
          answer:
            partialText && t.tools.length === 0 && partialText.length > t.answer.length ? partialText : t.answer,
          statusMessage: message || t.statusMessage,
          activeTool: activeTool ?? t.activeTool,
        }));
      }
      const id = `adopted_${now}`;
      const turn = newTurn(id, "", now, "streaming");
      turn.answer = partialText ?? "";
      turn.statusMessage = message;
      turn.activeTool = activeTool;
      if (activeTool) turn.tools.push(toolRow(`${id}_tool`, activeTool, textEnd(turn.answer)));
      return { turns: [...state.turns, turn], activeId: id };
    }),

  clearTranscript: () =>
    set({
      turns: [],
      activeId: null,
      notice: null,
      todos: NO_TODOS,
      todosTurnId: null,
      interrupted: false,
      waitingApproval: null,
      context: null,
    }),
}));

export function selectPendingApproval(state: { turns: Turn[]; activeId: string | null }): TurnApproval | null {
  if (state.activeId === null) return null;
  const active = state.turns.find((t) => t.id === state.activeId);
  if (!active?.approval || active.approval.state !== "request") return null;
  return active.approval;
}

/** The turn Retry sends again as a new turn: the last thing the user asked, with its picked apps. */
export function lastUserTurn(turns: Turn[]): Turn | null {
  for (let i = turns.length - 1; i >= 0; i -= 1) {
    if (turns[i].userText) return turns[i];
  }
  return null;
}

export function lastUserText(turns: Turn[]): string | null {
  return lastUserTurn(turns)?.userText ?? null;
}
