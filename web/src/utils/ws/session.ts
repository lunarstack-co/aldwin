import { HISTORY_LIMIT } from "@/constants/chat";
import { lastUserText, useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { PROTOCOL_VERSION, type Event, type StatusEvent } from "@/types/protocol";

import { createConnection, type ConnectionDeps } from "./connection";
import { requestId } from "./index";

type Reply = (event: Event) => void;
type Ask = "hello" | "history" | "status" | "sessions.new" | "sessions.list";

export type ChatSession = {
  start: () => void;
  dispose: () => void;
  sendTurn: (text: string) => void;
  stop: () => void;
  retry: () => void;
  newChat: () => void;
  openConversation: (id: string) => void;
  takeOver: () => void;
  reconnect: () => void;
};

/**
 * The chat over the socket: hello, then history; after a drop, status and
 * history to catch up. Turn events go to the active turn in the store, and
 * approvals are left alone for the screen that owns them.
 */
export function createChatSession(
  deps?: ConnectionDeps,
  now: () => number = () => Date.now(),
): ChatSession {
  const replies = new Map<string, Reply>();
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

  function handleOpen(catchUp: boolean) {
    ask("hello", (event) => {
      if (event.kind !== "done") return;
      if (!catchUp) {
        loadHistory();
        return;
      }
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
    chat().adoptRunningTurn(status.partial_text, status.active_tool, now());
    expectedRe = null;
  }

  function loadHistory() {
    ask("history", (event) => {
      if (event.kind === "history") chat().loadHistory(event.messages);
      if (event.kind === "done" || event.kind === "error") {
        useConnectionStore.getState().setPhase("open", 0);
        refreshSessions();
      }
    });
  }

  function refreshSessions() {
    ask("sessions.list", (event) => {
      if (event.kind === "sessions") chat().setSessions(event.items, event.current);
    });
  }

  function handleEvent(event: Event) {
    const reply = replies.get(event.re);
    if (reply) {
      reply(event);
      if (event.kind === "done" || event.kind === "error") replies.delete(event.re);
      return;
    }
    const store = chat();
    if (store.activeId === null) return;
    if (expectedRe !== null && event.re !== expectedRe) return;
    switch (event.kind) {
      case "text":
        store.appendText(event.delta);
        break;
      case "tool_call":
        store.addToolCall(event);
        break;
      case "tool_result":
        store.addToolResult(event);
        break;
      case "done":
        store.finishTurn(event.reason, now());
        expectedRe = null;
        // The first turn gives the conversation its title.
        refreshSessions();
        break;
      case "error":
        store.failTurn(event.message, now());
        expectedRe = null;
        break;
      default:
        break;
    }
  }

  function sendTurn(text: string) {
    const cleaned = text.trim();
    if (!cleaned) return;
    const id = requestId("turn");
    expectedRe = id;
    chat().startTurn(id, cleaned, now());
    connection.send({ type: "turn", id, text: cleaned, skills: [] });
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
      const text = lastUserText(chat().turns);
      if (text !== null) sendTurn(text);
    },
    newChat: () => {
      if (chat().turns.length === 0) return;
      ask("sessions.new", (event) => {
        if (event.kind !== "done") return;
        chat().clearTranscript();
        refreshSessions();
      });
    },
    openConversation: (id) => {
      const store = chat();
      if (store.activeId !== null || id === store.currentSessionId) return;
      const reqId = requestId("sessions_open");
      replies.set(reqId, (event) => {
        if (event.kind === "done") loadHistory();
        // A conversation deleted from another window drops out of the rail.
        if (event.kind === "error") refreshSessions();
      });
      connection.send({ type: "sessions.open", id: reqId, session_id: id });
    },
    takeOver: connection.takeOver,
    reconnect: connection.retry,
  };
}
