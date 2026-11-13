// Pure helpers, run in Node.
import { expect, test } from "@playwright/test";

import { RECONNECT } from "../src/constants/chat";
import { conversationHref, settingsHref } from "../src/constants/routes";
import { joinParagraph, lastUserText, useChatStore } from "../src/state/chat-store";
import { isConversationId } from "../src/utils/conversation-id";
import { readDensity, isDensity } from "../src/utils/density";
import { displayTitle, filterSessions, groupSessions, shouldCallSessionsNew } from "../src/utils/history";
import { allowsProtocol, allowsTag } from "../src/utils/markdown";
import { pageTitle } from "../src/utils/page-title";
import { markFor } from "../src/components/brand/brand-mark";
import { codeLanguages, resolveCodeLanguage } from "../src/components/assistant-ui/elements/shiki-bundle";
import { changedLimits, sectionOf } from "../src/utils/settings";
import { searchSkills, skillFormatter, skillsFromText, visibleSkills } from "../src/utils/skills";
import { turnsToMessages } from "../src/utils/thread-messages";
import { formatDuration } from "../src/utils/time";
import { planCarriesOver, planVisibility } from "../src/utils/todos";
import { outcomeOfClose, retryDelay, socketUrl } from "../src/utils/ws";
import { createConnection, type SocketLike } from "../src/utils/ws/connection";
import { ApiError } from "../src/utils/api";
import { SKILL_FIXTURE, session, skillsOf } from "./fixtures/fake-host";

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

test("skill tokens follow the CLI pattern", () => {
  expect(skillsFromText("@files and @web-research")).toEqual(["files", "web-research"]);
  expect(skillsFromText("mail fola@cuvama.com and x@files")).toEqual([]);
  expect(skillsFromText("@memory @files @memory")).toEqual(["memory", "files"]);
  expect(skillsFromText("@Files @-bad @a--b @ok")).toEqual(["ok"]);
  expect(skillsFromText("@ghost")).toEqual(["ghost"]);
  expect(skillsFromText("use @files, then\n@memory.")).toEqual(["files", "memory"]);
  expect(skillsFromText("npm i @types/node and use @vercel/og")).toEqual([]);
  expect(skillsFromText("see @files/notes or contact @acme.com today")).toEqual([]);
  expect(skillsFromText("ping @files! then @memory.")).toEqual(["files", "memory"]);
});

test("the menu hides broken skills and lists recent ones first", () => {
  expect(visibleSkills(SKILL_FIXTURE).map((s) => s.name)).toEqual(["web-research", "files", "memory", "ship-notes"]);
  expect(searchSkills(SKILL_FIXTURE, "we").map((s) => s.name)).toEqual(["web-research"]);
  expect(searchSkills(SKILL_FIXTURE, "es").map((s) => s.name)).toEqual(["web-research", "files", "ship-notes"]);
  expect(searchSkills(skillsOf(12), "")).toHaveLength(8);
  expect(searchSkills(skillsOf(12), "s1").map((s) => s.name)).toEqual(["s10", "s11", "s12"]);
});

test("skill formatter writes and reads @name", () => {
  expect(skillFormatter.serialize({ id: "files", type: "skill", label: "files" })).toBe("@files");
  expect(skillFormatter.parse("use @files now")).toEqual([
    { kind: "text", text: "use " },
    { kind: "mention", type: "skill", id: "files", label: "files" },
    { kind: "text", text: " now" },
  ]);
  expect(skillFormatter.parse("plain")).toEqual([{ kind: "text", text: "plain" }]);
});

test("settings addresses and sections", () => {
  expect(settingsHref()).toBe("/settings/");
  expect(settingsHref("skills")).toBe("/settings/?section=skills");
  expect(sectionOf("search")).toBe("search");
  expect(sectionOf("nope")).toBe("providers");
  expect(sectionOf(null)).toBe("providers");
});

test("only changed limits are sent", () => {
  const saved = { context_exchanges: 20, profile_chars: 4000, notes_chars: 4000, notes_days: 7, persona_chars: 2000, max_steps: 15, web_searches: 5, connector_tools: 40 };
  expect(changedLimits(saved, { ...saved, max_steps: 20 })).toEqual({ max_steps: 20 });
  expect(changedLimits(saved, saved)).toEqual({});
});

test("brand marks by host name, none for unknown names", () => {
  expect(markFor("openai")?.title).toBe("OpenAI");
  expect(markFor("Tavily")?.title).toBe("Tavily");
  expect(markFor("brave")?.title).toBe("Brave");
  // App marks load on first use, so they are not among the bundled ones.
  expect(markFor("linear")).toBeNull();
  expect(markFor("acme")).toBeNull();
});

test("only connected apps that are on and have tools can be picked", async () => {
  const { pickableApps, searchApps } = await import("../src/utils/apps");
  const tool = (name: string, effective: "auto" | "ask" | "off" = "auto") => ({
    name,
    model_name: `mcp__x__${name}`,
    title: null,
    description: "",
    read_only: true,
    setting: null,
    effective,
  });
  const base = {
    url: null,
    auth: "none" as const,
    trust_read_only_hints: true,
    error: null,
    retry_in_s: null,
    transport: "http" as const,
    command: null,
    note: null,
  };
  const apps = pickableApps([
    { ...base, name: "linear", title: "Linear", enabled: true, status: "connected", tools: [tool("a"), tool("b", "off")] },
    { ...base, name: "github", title: "GitHub", enabled: true, status: "connected", tools: [tool("c")] },
    { ...base, name: "empty", title: "Empty", enabled: true, status: "connected", tools: [tool("d", "off")] },
    { ...base, name: "off", title: "Off", enabled: false, status: "disabled", tools: [tool("e")] },
    { ...base, name: "notion", title: "Notion", enabled: true, status: "needs_auth", tools: [tool("f")] },
  ]);
  expect(apps).toEqual([
    { name: "linear", title: "Linear", toolCount: 1 },
    { name: "github", title: "GitHub", toolCount: 1 },
  ]);
  expect(searchApps(apps, "git").map((a) => a.name)).toEqual(["github"]);
  expect(searchApps(apps, "").length).toBe(2);
});

test("approval store transitions request -> running -> done and clears on turn end", () => {
  const store = useChatStore.getState();
  store.clearTranscript();
  store.startTurn("t_app", "run ls", 1000);
  store.addToolCall({
    v: 2,
    type: "event",
    re: "t_app",
    channel: "web",
    sender: "host",
    kind: "tool_call",
    call_id: "c1",
    name: "run_command",
    args_preview: "ls",
    summary: "",
  });
  store.setApproval({
    v: 2,
    type: "event",
    re: "t_app",
    channel: "web",
    sender: "host",
    kind: "approval",
    approval_id: "ap_1",
    call_id: "c1",
    tool: "run_command",
    tier: "ask",
    summary: "Run ls",
    digest: "sha256:abc",
  });
  expect(useChatStore.getState().turns[0].approval?.state).toBe("request");

  store.answerApproval("yes");
  expect(useChatStore.getState().turns[0].approval?.state).toBe("running");

  store.addToolResult({
    v: 2,
    type: "event",
    re: "t_app",
    channel: "web",
    sender: "host",
    kind: "tool_result",
    call_id: "c1",
    ok: true,
    summary: "exit 0",
    preview: "",
    duration_ms: 10,
  });
  expect(useChatStore.getState().turns[0].approval?.state).toBe("done");

  store.finishTurn("answered", 2000);
  expect(useChatStore.getState().turns[0].approval).toBeNull();
  expect(useChatStore.getState().turns[0].approvalsByCallId.c1?.state).toBe("done");
});

test("approval arriving before its tool_call attaches once the row arrives", () => {
  const store = useChatStore.getState();
  store.clearTranscript();
  store.startTurn("t_pre", "edit", 1000);
  store.setApproval({
    v: 2,
    type: "event",
    re: "t_pre",
    channel: "web",
    sender: "host",
    kind: "approval",
    approval_id: "ap_pre",
    call_id: "c1",
    tool: "edit_file",
    tier: "diff",
    summary: "Edit notes.md",
    digest: "sha256:pre",
  });
  store.addToolCall({
    v: 2,
    type: "event",
    re: "t_pre",
    channel: "web",
    sender: "host",
    kind: "tool_call",
    call_id: "c1",
    name: "edit_file",
    args_preview: "notes.md",
    summary: "",
  });

  const [, assistant] = turnsToMessages(useChatStore.getState().turns);
  const parts = assistant.content as unknown as { artifact?: { approval?: { event: { approval_id: string } } } }[];
  expect(parts[0].artifact?.approval?.event.approval_id).toBe("ap_pre");
});

test("todo status mapping, context threshold, day labels and message timing", async () => {
  const { mapTodoStatus } = await import("../src/utils/todos");
  const { contextTone } = await import("../src/utils/context");
  const { computeMessageTiming, formatDayLabel } = await import("../src/utils/time");

  expect(mapTodoStatus("pending")).toBe("pending");
  expect(mapTodoStatus("in_progress")).toBe("active");
  expect(mapTodoStatus("done")).toBe("done");
  expect(mapTodoStatus("dropped")).toBe("cancelled");

  expect(contextTone(108_800, 128_000)).toBe("normal");
  expect(contextTone(108_801, 128_000)).toBe("amber");

  const now = new Date("2026-11-12T12:00:00Z");
  expect(formatDayLabel("2026-11-12T09:00:00Z", { now, locale: "en-GB" })).toBe("Today");
  expect(formatDayLabel("2026-11-11T09:00:00Z", { now, locale: "en-GB" })).toBe("Yesterday");
  expect(formatDayLabel("2026-11-09T09:00:00Z", { now, locale: "en-GB" })).toBe("9 Nov 2026");

  const timing = computeMessageTiming(1000, 1800, 5800, 200);
  expect(timing?.firstTokenSeconds).toBe("0.8 s");
  expect(timing?.tokensPerSecond).toBe("50 tok/s");
});

test("code fences resolve to a bundled grammar or plain text", () => {
  expect(resolveCodeLanguage("python")).toBe("python");
  expect(resolveCodeLanguage(" PY ")).toBe("python");
  expect(resolveCodeLanguage("bash")).toBe("shellscript");
  expect(resolveCodeLanguage("yml")).toBe("yaml");
  expect(resolveCodeLanguage("golang")).toBe("go");
  expect(resolveCodeLanguage("cobol")).toBe("text");
  expect(resolveCodeLanguage("")).toBe("text");
  expect(resolveCodeLanguage("constructor")).toBe("text");
  expect(Object.keys(codeLanguages)).toHaveLength(36);
});

test("text after a tool call starts a new paragraph", () => {
  expect(joinParagraph("I'll search first.", "The latest release")).toBe("I'll search first.\n\nThe latest release");
  expect(joinParagraph("Done.\n", " Next")).toBe("Done.\n\nNext");
  expect(joinParagraph("", "First words")).toBe("First words");
  const store = useChatStore.getState();
  store.clearTranscript();
  store.startTurn("t1", "q", 1);
  store.appendText("I'll search, then check the source.", 2);
  store.addToolCall({ v: 2, type: "event", re: "t1", kind: "tool_call", call_id: "c1", name: "web_search", args_preview: "", summary: "" } as never);
  store.appendText("The latest", 3);
  store.appendText(" release", 4);
  expect(useChatStore.getState().turns.at(-1)?.answer).toBe("I'll search, then check the source.\n\nThe latest release");
});

test("the plan shows for this turn's work or a carried-over answer, never for stopped or older work", () => {
  const open = [{ status: "done" as const }, { status: "pending" as const }];
  const finished = [{ status: "done" as const }, { status: "dropped" as const }];
  expect(planVisibility([], true, true, true)).toBe("hidden");
  expect(planVisibility(finished, true, true, false)).toBe("shown");
  expect(planVisibility(finished, true, false, true)).toBe("hidden");
  expect(planVisibility(open, true, false, true)).toBe("shown");
  expect(planVisibility(open, true, false, false)).toBe("hidden");
  expect(planVisibility(open, false, false, true)).toBe("shown");
  expect(planVisibility(open, false, false, false)).toBe("hidden");

  const answered = { id: "t1", status: "answered" };
  const stopped = { id: "t1", status: "stopped" };
  expect(planCarriesOver("t1", answered)).toBe(true);
  expect(planCarriesOver("t1", stopped)).toBe(false);
  expect(planCarriesOver("t0", answered)).toBe(false);
  expect(planCarriesOver(null, answered)).toBe(true);
  expect(planCarriesOver(null, undefined)).toBe(true);
  expect(planCarriesOver(null, stopped)).toBe(false);
});

test("a reconnect keeps the tab's text once tools have run, so tool positions stay right", () => {
  const store = useChatStore.getState();
  store.clearTranscript();
  store.startTurn("t1", "q", 1);
  store.appendText("Let me look.", 2);
  store.addToolCall({ re: "t1", kind: "tool_call", call_id: "c1", name: "grep", args_preview: "", summary: "" } as never);
  store.appendText("Found it.", 3);
  const before = useChatStore.getState().turns[0].answer;
  store.adoptRunningTurn("Let me look.Found it. And more text from the host", null, 4);
  expect(useChatStore.getState().turns[0].answer).toBe(before);

  store.clearTranscript();
  store.startTurn("t2", "q", 1);
  store.appendText("Let me", 2);
  store.adoptRunningTurn("Let me look at it", null, 3);
  expect(useChatStore.getState().turns[0].answer).toBe("Let me look at it");
});
