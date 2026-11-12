import { HISTORY_LIMIT } from "@/constants/chat";
import { lastUserTurn, useChatStore, type PickedApp } from "@/state/chat-store";
import commonCopy from "@/data/settings/common.json";
import { useConnectionStore } from "@/state/connection-store";
import { useSettingsStore } from "@/state/settings-store";
import {
  PROTOCOL_VERSION,
  type Event,
  type OutboundRequest,
  type StatusEvent,
  type TodoUpdatedEvent,
} from "@/types/protocol";
import { shouldCallSessionsNew } from "@/utils/history";
import { skillsFromText } from "@/utils/skills";

import { createConnection, type ConnectionDeps } from "./connection";
import { requestId } from "./index";

type Reply = (event: Event) => void;

type WithoutId<R> = R extends unknown ? Omit<R, "id"> : never;
/** A request as a caller builds it; the session gives it its id. */
export type RequestBody = WithoutId<OutboundRequest>;
/** How the host answered a request: `done`, or `error` with its code and sentence. */
export type Outcome = { ok: true } | { ok: false; code: string; message: string };

function todosOf(event: TodoUpdatedEvent) {
  return { items: event.items, done: event.done, total: event.total };
}
type Ask = "hello" | "history" | "status" | "sessions.new" | "sessions.list" | "todo.get" | "skills.list";

export type ChatSession = {
  start: () => void;
  dispose: () => void;
  sendTurn: (text: string, apps?: PickedApp[]) => void;
  stop: () => void;
  retry: () => void;
  /** Starts a new chat, or keeps the empty one; `onSettled` hears whether the host agreed. */
  newChat: (onSettled?: (started: boolean) => void) => void;
  /** Opens a saved conversation; `onSettled` hears whether the host switched. */
  openConversation: (id: string, onSettled?: (opened: boolean) => void) => void;
  refreshSessions: () => void;
  /** Asks the host for its skill catalogue, for the `@` menu. */
  listSkills: () => void;
  /**
   * Sends one request; events tied to it go to `onEvent`, and the promise
   * settles with the host's `done` or `error`, or as dropped if the socket goes.
   */
  request: (body: RequestBody, onEvent?: Reply) => Promise<Outcome>;
  /** Ask again for a settings list after a change; the list on screen stays until the new one lands. */
  refreshConnectors: () => void;
  refreshModels: (probe?: boolean) => void;
  refreshSearch: () => void;
  takeOver: () => void;
  reconnect: () => void;
};

/**
 * The chat over the socket: hello, then history; after a drop, status and
 * history to catch up. Turn events go to the active turn in the store, and
 * approvals are shown as waiting, since the web cannot answer them yet.
 */
export function createChatSession(
  deps?: ConnectionDeps,
  now: () => number = () => Date.now(),
): ChatSession {
  const replies = new Map<string, Reply>();
  // Callers waiting on `request`, so a dropped socket can answer them instead of leaving them hanging.
  const pending = new Map<string, (outcome: Outcome) => void>();
  const settings = () => useSettingsStore.getState();
  // The turn this tab started; `null` while an adopted turn accepts any id.
  let expectedRe: string | null = null;
  const chat = () => useChatStore.getState();

  const connection = createConnection(
    {
      onPhase: (phase, attempt) => useConnectionStore.getState().setPhase(phase, attempt),
      onEvent: handleEvent,
      onOpen: handleOpen,
    },
    deps,
  );

  function ask(type: Ask, reply: Reply) {
    const id = requestId(type.replace(".", "_"));
    replies.set(id, reply);
    if (type === "hello") {
      connection.send({ type, id, v_min: PROTOCOL_VERSION, v_max: PROTOCOL_VERSION });
    } else if (type === "history") {
      connection.send({ type, id, limit: HISTORY_LIMIT });
    } else {
      connection.send({ type, id });
    }
  }

  function handleOpen() {
    // Requests sent on a dropped socket will never be answered.
    replies.clear();
    for (const settle of pending.values()) settle({ ok: false, code: "dropped", message: commonCopy.dropped });
    pending.clear();
    settings().forgetLoading();
    // A catalogue request lost with the old socket is asked again once something wants it.
    if (chat().skills.status === "loading") chat().setSkillsStatus("idle");
    ask("hello", (event) => {
      if (event.kind !== "done") return;
      refreshSettings();
      // A turn may be running from before this page opened, not only from before a drop.
      ask("status", (status) => {
        if (status.kind === "status") adopt(status);
        if (status.kind === "done" || status.kind === "error") loadHistory();
      });
    });
  }

  function adopt(status: StatusEvent) {
    const running =
      status.turn_streamed_chunks > 0 || status.active_tool !== null || status.partial_text !== null;
    if (!running) return;
    chat().adoptRunningTurn(status.partial_text, status.active_tool, now(), status.message);
    expectedRe = null;
  }

  function loadHistory() {
    ask("history", (event) => {
      if (event.kind === "history") chat().loadHistory(event.messages, event.interrupted);
      if (event.kind === "done" || event.kind === "error") {
        useConnectionStore.getState().setPhase("open", 0);
        refreshSessions();
        refreshTodos();
      }
    });
  }

  function refreshTodos() {
    ask("todo.get", (event) => {
      if (event.kind === "todo.updated") chat().setTodos(todosOf(event));
    });
  }

  function listSkills() {
    chat().setSkillsStatus("loading");
    ask("skills.list", (event) => {
      if (event.kind === "skills") chat().setSkills(event.items);
      if (event.kind === "error") chat().setSkillsStatus("error");
    });
  }

  /**
   * Keeps the settings lists current from any event that carries one: unsolicited
   * ones (another window or the terminal changed something) and replies to this
   * page's own requests. Returns whether the event was a settings event.
   */
  function applySettingsEvent(event: Event): boolean {
    switch (event.kind) {
      case "models":
        settings().setModels({ current: event.current, providers: event.providers });
        return true;
      case "search.providers":
        settings().setSearch({ setting: event.setting, active: event.active, items: event.items });
        return true;
      case "connectors":
        settings().setConnectors({ items: event.items, catalog: event.catalog });
        return true;
      case "connector.status":
        settings().applyConnectorStatus(event);
        // A connector that just connected has new tools; the list carries them.
        if (event.status === "connected" && settings().connectors.status === "ready") refreshConnectors();
        return true;
      default:
        return false;
    }
  }

  function request(body: RequestBody, onEvent?: Reply): Promise<Outcome> {
    return new Promise((resolve) => {
      const id = requestId(body.type.replace(/\./g, "_"));
      pending.set(id, resolve);
      replies.set(id, (event) => {
        if (event.kind === "done") resolve({ ok: true });
        else if (event.kind === "error") resolve({ ok: false, code: event.code, message: event.message });
        else {
          applySettingsEvent(event);
          onEvent?.(event);
        }
        if (event.kind === "done" || event.kind === "error") pending.delete(id);
      });
      connection.send({ ...body, id } as OutboundRequest);
    });
  }

  // Asks for a list again without a loading state, so what is on screen stays until the new list lands.
  function refreshModels(probe = false) {
    void request({ type: "models.list", ...(probe ? { refresh: true } : {}) });
  }

  function refreshSearch() {
    void request({ type: "search.list" });
  }

  // Settings lists already on screen may be out of date after a drop; they are asked again without a loading state.
  function refreshSettings() {
    const { models, search, connectors } = settings();
    if (models.status === "ready") refreshModels();
    if (search.status === "ready") refreshSearch();
    if (connectors.status === "ready") refreshConnectors();
  }

  function refreshConnectors() {
    void request({ type: "connectors.list" });
  }

  function refreshSessions() {
    ask("sessions.list", (event) => {
      if (event.kind === "sessions") chat().setSessions(event.items, event.current);
    });
  }

  /**
   * The reply to an open or a new chat. The host owns the decision: it says
   * `busy` while a turn runs and `not_found` for a conversation deleted
   * elsewhere, and either way this tab keeps what it is showing.
   */
  function switchReply(onDone: () => void, onSettled?: (opened: boolean) => void): Reply {
    return (event) => {
      if (event.kind === "done") {
        chat().setNotice(null);
        chat().setHistoryView("chat");
        onDone();
        onSettled?.(true);
      } else if (event.kind === "error") {
        const code = event.code === "busy" || event.code === "not_found" ? event.code : "other";
        chat().setNotice({ code, message: event.message });
        refreshSessions();
        onSettled?.(false);
      }
    };
  }

  function handleEvent(event: Event) {
    const reply = replies.get(event.re);
    if (reply) {
      reply(event);
      if (event.kind === "done" || event.kind === "error") replies.delete(event.re);
      return;
    }
    const store = chat();
    // These belong to the conversation, not to one turn, so they land between turns too.
    if (event.kind === "notice") {
      store.pushToast(event.message);
      return;
    }
    if (event.kind === "todo.updated") {
      store.setTodos(todosOf(event));
      return;
    }
    // The host sends a fresh catalogue after a skill is created.
    if (event.kind === "skills") {
      store.setSkills(event.items);
      return;
    }
    if (event.kind === "skills.used") {
      store.setSkillsUsed(event.re, event.names, event.dropped);
      return;
    }
    if (event.kind === "apps.used") {
      store.setAppsUsed(event.re, event.names, event.dropped);
      return;
    }
    if (applySettingsEvent(event)) return;
    if (store.activeId === null) return;
    if (expectedRe !== null && event.re !== expectedRe) return;
    switch (event.kind) {
      case "text":
        store.appendText(event.delta);
        break;
      case "approval":
        store.setWaitingApproval(event.summary);
        break;
      case "tool_call":
        store.addToolCall(event);
        break;
      case "tool_result":
        store.addToolResult(event);
        break;
      case "status":
        store.setTurnStatus(event.message, event.active_tool);
        break;
      case "done":
        store.finishTurn(event.reason, now(), { usage: event.usage, maxSteps: event.max_steps });
        expectedRe = null;
        // The first turn gives the conversation its title.
        refreshSessions();
        break;
      case "error":
        if (event.code === "busy" && store.activeId === event.re) {
          // The host is still running an earlier turn this page did not know about: follow that one instead.
          store.refuseTurn(event.re);
          store.pushToast(event.message);
          store.adoptRunningTurn(null, null, now());
          expectedRe = null;
          loadHistory();
          break;
        }
        store.failTurn(event.message, now(), event.code);
        expectedRe = null;
        break;
      default:
        break;
    }
  }

  /** Sends a message; with no `apps` given it takes the ones picked in the composer. */
  function sendTurn(text: string, apps?: PickedApp[]) {
    const cleaned = text.trim();
    if (!cleaned) return;
    const picked = apps ?? chat().pickedApps;
    const id = requestId("turn");
    expectedRe = id;
    chat().startTurn(id, cleaned, now(), picked);
    // Send, Retry and pasted text all name their skills the same way: as `@name` in the text.
    connection.send({
      type: "turn",
      id,
      text: cleaned,
      skills: skillsFromText(cleaned),
      ...(picked.length > 0 ? { apps: picked.map((app) => app.name) } : {}),
    });
  }

  return {
    start: connection.start,
    dispose: connection.dispose,
    sendTurn,
    stop: () => {
      chat().markStopping();
      connection.send({ type: "stop", id: requestId("stop") });
    },
    retry: () => {
      const last = lastUserTurn(chat().turns);
      if (last) sendTurn(last.userText, last.pickedApps);
    },
    newChat: (onSettled) => {
      if (!shouldCallSessionsNew(chat().turns.length)) {
        chat().setHistoryView("chat");
        onSettled?.(true);
        return;
      }
      ask(
        "sessions.new",
        switchReply(() => {
          chat().clearTranscript();
          refreshSessions();
        }, onSettled),
      );
    },
    openConversation: (id, onSettled) => {
      if (id === chat().currentSessionId) {
        chat().setHistoryView("chat");
        onSettled?.(true);
        return;
      }
      const reqId = requestId("sessions_open");
      replies.set(reqId, switchReply(loadHistory, onSettled));
      connection.send({ type: "sessions.open", id: reqId, session_id: id });
    },
    refreshSessions,
    listSkills,
    request,
    refreshConnectors,
    refreshModels,
    refreshSearch,
    takeOver: connection.takeOver,
    reconnect: connection.retry,
  };
}
