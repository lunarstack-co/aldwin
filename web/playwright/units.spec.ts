// Pure helpers, run in Node; the same cases chat-units.cy.ts covered, plus the new ones.
import { expect, test } from "@playwright/test";

import { RECONNECT } from "../src/constants/chat";
import { conversationHref } from "../src/constants/routes";
import { lastUserText, useChatStore } from "../src/state/chat-store";
import { isConversationId } from "../src/utils/conversation-id";
import { readDensity, isDensity } from "../src/utils/density";
import { displayTitle, filterSessions, groupSessions, shouldCallSessionsNew } from "../src/utils/history";
import { allowsProtocol, allowsTag } from "../src/utils/markdown";
import { pageTitle } from "../src/utils/page-title";
import { turnsToMessages } from "../src/utils/thread-messages";
import { formatDuration } from "../src/utils/time";
import { outcomeOfClose, retryDelay, socketUrl } from "../src/utils/ws";
import { createConnection, type SocketLike } from "../src/utils/ws/connection";
import { ApiError } from "../src/utils/api";
import { session } from "./fixtures/fake-host";

test("formatDuration", () => {
  const cases: [number, string][] = [
    [0, "0 ms"], [840, "840 ms"], [999, "999 ms"], [1000, "1.0 s"], [12_400, "12.4 s"],
    [59_949, "59.9 s"], [60_000, "1 min 0 s"], [75_000, "1 min 15 s"],
  ];
  for (const [ms, text] of cases) expect(formatDuration(ms)).toBe(text);
});

test("conversation ids", () => {
  expect(isConversationId("abc_D-1")).toBe(true);
  expect(isConversationId("a".repeat(128))).toBe(true);
  for (const bad of ["", "a".repeat(129), "a b", "../x", null]) expect(isConversationId(bad)).toBe(false);
  expect(conversationHref("s1")).toBe("/c/?id=s1");
});

test("page titles", () => {
  const base = { app: "Aldwin", fallback: "Conversation" };
  expect(pageTitle({ ...base, title: "", firstUserText: "Hello there", running: true })).toBe("● Hello there · Aldwin");
  expect(pageTitle({ ...base, title: "", firstUserText: "", running: false })).toBe("Conversation · Aldwin");
  expect(pageTitle({ ...base, title: "Trip", firstUserText: "x", running: false })).toBe("Trip · Aldwin");
});

test("sanitiser keeps prose and links but no script, frames, styles or script urls", () => {
  for (const tag of ["script", "iframe", "style"]) expect(allowsTag(tag)).toBe(false);
  for (const tag of ["a", "table", "code"]) expect(allowsTag(tag)).toBe(true);
  expect(allowsProtocol("href", "javascript")).toBe(false);
  expect(allowsProtocol("href", "https")).toBe(true);
});

test("density defaults to clean and only accepts the two known values", () => {
  const store = (value: string | null) => ({ getItem: () => value, setItem: () => {} }) as unknown as Storage;
  expect(readDensity(store(null))).toBe("clean");
  expect(readDensity(store("detail"))).toBe("detail");
  expect(readDensity(store("loud"))).toBe("clean");
  expect(isDensity("clean")).toBe(true);
  expect(isDensity(1)).toBe(false);
});

test("history helpers", () => {
  expect(displayTitle(session("a", ""), "Untitled chat")).toBe("Untitled chat");
  const rows = [session("a", "Plan the trip"), session("b", "Groceries")];
  expect(filterSessions(rows, "PLAN", "x").map((s) => s.id)).toEqual(["a"]);
  expect(filterSessions(rows, "  ", "x")).toHaveLength(2);
  const groups = groupSessions(
    [
      session("t", "", "2026-11-11T08:00:00"),
      session("y", "", "2026-11-10T08:00:00"),
      session("w", "", "2026-11-06T08:00:00"),
      session("o", "", "2026-10-20T08:00:00"),
    ],
    new Date("2026-11-11T10:00:00"),
  );
  expect([groups.today, groups.yesterday, groups.previous7, groups.older].map((g) => g.map((s) => s.id))).toEqual([["t"], ["y"], ["w"], ["o"]]);
  expect(shouldCallSessionsNew(0)).toBe(false);
  expect(shouldCallSessionsNew(1)).toBe(true);
});

test("retry adds a turn with the last question and history groups into turns", () => {
  const store = useChatStore.getState();
  store.clearTranscript();
  store.loadHistory([
    { role: "user", text: "q1" },
    { role: "tool", text: "✓ grep" },
    { role: "assistant", text: "a1" },
    { role: "user", text: "q2" },
    { role: "assistant", text: "a2" },
  ]);
  const turns = useChatStore.getState().turns;
  expect(turns).toHaveLength(2);
  expect(turns[0].tools).toHaveLength(1);
  expect(turns[0].tools[0].ok).toBe(true);
  expect(lastUserText(turns)).toBe("q2");
});

test("thread messages carry tool results and their source", () => {
  const store = useChatStore.getState();
  store.clearTranscript();
  store.startTurn("t1", "q", 1000);
  store.addToolCall({ v: 2, type: "event", re: "t1", channel: "web", sender: "host", kind: "tool_call", call_id: "c1", name: "search", args_preview: "x", summary: "", connector: "gh", connector_title: "GitHub" });
  store.addToolResult({ v: 2, type: "event", re: "t1", channel: "web", sender: "host", kind: "tool_result", call_id: "c1", ok: true, summary: "3", preview: "p", duration_ms: 12 });
  store.appendText("done");
  store.finishTurn("answered", 2000, { usage: { input_tokens: 1, output_tokens: 2 } });
  const [user, assistant] = turnsToMessages(useChatStore.getState().turns);
  expect(user.role).toBe("user");
  const parts = assistant.content as unknown as { type: string; result?: unknown; artifact?: unknown }[];
  expect(parts.map((p) => p.type)).toEqual(["tool-call", "text"]);
  expect(parts[0].result).toEqual({ summary: "3", durationMs: 12, preview: "p" });
  expect(parts[0].artifact).toEqual({ connectorTitle: "GitHub", remoteTool: null, reason: null });
  expect(assistant.status).toEqual({ type: "complete", reason: "stop" });
  expect(useChatStore.getState().turns[0].usage).toEqual({ input_tokens: 1, output_tokens: 2 });
});

test("socket helpers", () => {
  expect(socketUrl("http://127.0.0.1:4173/c/?id=a", "t1", false)).toBe("ws://127.0.0.1:4173/v1/ws?ticket=t1");
  expect(socketUrl("http://127.0.0.1:4173/", "t1", true)).toContain("take_over=1");
  expect(outcomeOfClose(1001, 1, 5)).toBe("stopped");
  expect(outcomeOfClose(4000, 1, 5)).toBe("taken");
  expect(outcomeOfClose(4409, 1, 5)).toBe("taken");
  expect(outcomeOfClose(1006, 1, 5)).toBe("retry");
  expect(outcomeOfClose(1006, 5, 5)).toBe("stopped");
  expect(retryDelay(1)).toBe(500);
  expect(retryDelay(10)).toBe(8000);
});

test("connection reaches stopped after the retries run out, and not ready on 503", async () => {
  const phases: string[] = [];
  const sockets: SocketLike[] = [];
  const timers: (() => void)[] = [];
  let failTickets = false;
  const connection = createConnection(
    { onPhase: (phase) => phases.push(phase), onEvent: () => {}, onOpen: () => {} },
    {
      mintTicket: async () => {
        if (failTickets) throw new ApiError(502, "down");
        return "t";
      },
      openSocket: () => {
        const socket: SocketLike = { send: () => {}, close: () => {}, onopen: null, onmessage: null, onclose: null };
        sockets.push(socket);
        queueMicrotask(() => socket.onopen?.());
        return socket;
      },
      pageUrl: () => "http://127.0.0.1:4173/",
      schedule: (fn) => {
        timers.push(fn);
        return () => {};
      },
    },
  );
  connection.start();
  await new Promise((r) => setTimeout(r, 10));
  expect(phases.at(-1)).toBe("open");
  failTickets = true;
  sockets[0].onclose?.({ code: 1006 });
  for (let i = 0; i < RECONNECT.attempts; i += 1) {
    const next = timers.shift();
    if (!next) break;
    next();
    await new Promise((r) => setTimeout(r, 5));
  }
  expect(phases.at(-1)).toBe("stopped");

  const notReady: string[] = [];
  createConnection(
    { onPhase: (phase) => notReady.push(phase), onEvent: () => {}, onOpen: () => {} },
    {
      mintTicket: async () => {
        throw new ApiError(503, "not ready");
      },
      openSocket: () => sockets[0],
      pageUrl: () => "http://127.0.0.1:4173/",
      schedule: () => () => {},
    },
  ).start();
  await new Promise((r) => setTimeout(r, 10));
  expect(notReady.at(-1)).toBe("not_ready");
});
