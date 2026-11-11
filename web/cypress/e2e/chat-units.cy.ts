// The pure pieces behind the chat shell, run in the browser without a page.
import { lastUserText, useChatStore } from "@/state/chat-store";
import { ApiError } from "@/utils/api";
import { DEFAULT_DENSITY, readDensity, writeDensity } from "@/utils/density";
import { LINK_REL, allowsProtocol, allowsTag } from "@/utils/markdown";
import { assistantMessageId, turnIdOf, turnsToMessages, userMessageId } from "@/utils/thread-messages";
import { outcomeOfClose, retryDelay, socketUrl } from "@/utils/ws";
import { createConnection, type ConnectionDeps, type SocketLike } from "@/utils/ws/connection";

type FakeSocket = SocketLike & { url: string; closed: boolean };

function fakeDeps(tickets: () => Promise<string>) {
  const sockets: FakeSocket[] = [];
  const timers: (() => void)[] = [];
  const deps: ConnectionDeps = {
    mintTicket: tickets,
    pageUrl: () => "http://127.0.0.1:7841/",
    openSocket: (url) => {
      const socket: FakeSocket = {
        url,
        closed: false,
        send() {},
        close() {
          this.closed = true;
        },
        onopen: null,
        onmessage: null,
        onclose: null,
      };
      sockets.push(socket);
      return socket;
    },
    schedule: (fn) => {
      timers.push(fn);
      return () => {};
    },
  };
  return { deps, sockets, timers };
}

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("markdown sanitising", () => {
  it("keeps prose and links but no script, frames, styles or script URLs", () => {
    for (const tag of ["p", "a", "code", "pre", "ul", "table", "strong"]) expect(allowsTag(tag), tag).to.equal(true);
    for (const tag of ["script", "iframe", "style", "object", "embed", "form"]) expect(allowsTag(tag), tag).to.equal(false);
    expect(allowsProtocol("href", "https")).to.equal(true);
    expect(allowsProtocol("href", "javascript")).to.equal(false);
    expect(LINK_REL.split(" ")).to.include.members(["noopener", "noreferrer"]);
  });
});

describe("density", () => {
  it("defaults to clean and only accepts the two known values", () => {
    expect(DEFAULT_DENSITY).to.equal("clean");
    expect(readDensity(null)).to.equal("clean");
    expect(readDensity({ getItem: () => "detail", setItem() {} })).to.equal("detail");
    expect(readDensity({ getItem: () => "dense", setItem() {} })).to.equal("clean");
    expect(
      readDensity({
        getItem: () => {
          throw new Error("blocked");
        },
        setItem() {},
      }),
    ).to.equal("clean");
    const saved: Record<string, string> = {};
    writeDensity({ getItem: () => null, setItem: (k, v) => void (saved[k] = v) }, "detail");
    expect(saved).to.deep.equal({ "aldwin.density": "detail" });
  });
});

describe("chat store", () => {
  beforeEach(() => useChatStore.getState().clearTranscript());

  it("retries by adding a new turn with the last question; nothing is replaced", () => {
    const store = useChatStore.getState();
    store.startTurn("t1", "first", 1000);
    store.appendText("one");
    store.finishTurn("answered", 2000);
    store.startTurn("t2", "second", 3000);
    store.appendText("two");
    store.finishTurn("answered", 4000);
    expect(lastUserText(useChatStore.getState().turns)).to.equal("second");
    store.startTurn("t3", lastUserText(useChatStore.getState().turns) ?? "", 5000);
    const turns = useChatStore.getState().turns;
    expect(turns.map((t) => t.userText)).to.deep.equal(["first", "second", "second"]);
    expect(turns[1].answer).to.equal("two");
    expect(useChatStore.getState().activeId).to.equal("t3");
  });

  it("groups history rows into turns", () => {
    useChatStore.getState().loadHistory([
      { role: "user", text: "a" },
      { role: "tool", text: "✓ grep · 3 rows" },
      { role: "assistant", text: "A." },
      { role: "user", text: "b" },
      { role: "assistant", text: "B." },
    ]);
    const turns = useChatStore.getState().turns;
    expect(turns).to.have.length(2);
    expect(turns[0].tools[0]).to.include({ name: "grep · 3 rows", ok: true });
    expect(turns[1].answer).to.equal("B.");
  });

  it("folds the host's running turn into the history it just loaded", () => {
    const store = useChatStore.getState();
    store.adoptRunningTurn("Hel", "read_file", 10);
    store.appendText("lo");
    store.loadHistory([
      { role: "user", text: "old" },
      { role: "assistant", text: "Old." },
      { role: "user", text: "now" },
    ]);
    const turns = useChatStore.getState().turns;
    expect(turns).to.have.length(2);
    expect(turns[1]).to.include({ userText: "now", answer: "Hello", status: "streaming" });
    expect(turns[1].tools[0].name).to.equal("read_file");
    store.finishTurn("stopped", 20);
    expect(useChatStore.getState().turns[1].status).to.equal("stopped");
  });
});

describe("thread messages", () => {
  beforeEach(() => useChatStore.getState().clearTranscript());

  it("turns the transcript into user and assistant messages the runtime understands", () => {
    const store = useChatStore.getState();
    store.startTurn("t1", "find it", 1000);
    store.addToolCall({ v: 2, type: "event", re: "t1", channel: "web", sender: "host", kind: "tool_call", call_id: "c1", name: "grep", args_preview: "needle", summary: "" });
    store.addToolResult({ v: 2, type: "event", re: "t1", channel: "web", sender: "host", kind: "tool_result", call_id: "c1", ok: true, summary: "3 rows", preview: "", duration_ms: 120 });
    store.appendText("Found.");
    const streaming = turnsToMessages(useChatStore.getState().turns);
    expect(streaming.map((m) => m.id)).to.deep.equal([userMessageId("t1"), assistantMessageId("t1")]);
    expect(streaming[1].status).to.deep.equal({ type: "running" });
    const parts = streaming[1].content as readonly { type: string }[];
    expect(parts.map((p) => p.type)).to.deep.equal(["tool-call", "text"]);
    expect(parts[0]).to.include({ toolCallId: "c1", toolName: "grep", argsText: "needle", isError: false });
    expect(parts[0]).to.have.nested.property("result.summary", "3 rows");

    store.finishTurn("stopped", 2000);
    expect(turnsToMessages(useChatStore.getState().turns)[1].status).to.deep.equal({ type: "incomplete", reason: "cancelled" });
    store.startTurn("t2", "again", 3000);
    store.failTurn("busy", 4000);
    const failed = turnsToMessages(useChatStore.getState().turns)[3];
    expect(failed.status).to.deep.equal({ type: "incomplete", reason: "error", error: "busy" });
    expect(turnIdOf(userMessageId("t2"))).to.equal("t2");
    expect(turnIdOf(assistantMessageId("t2"))).to.equal("t2");
  });
});

describe("socket helpers", () => {
  it("builds the socket address from the page address", () => {
    expect(socketUrl("http://127.0.0.1:7841/?t=abc", "tk", false)).to.equal("ws://127.0.0.1:7841/v1/ws?ticket=tk");
    expect(socketUrl("https://localhost:7841/", "tk", true)).to.equal("wss://localhost:7841/v1/ws?ticket=tk&take_over=1");
  });

  it("decides what a close means", () => {
    expect(outcomeOfClose(4409, 1, 5)).to.equal("taken");
    expect(outcomeOfClose(4000, 1, 5)).to.equal("taken");
    expect(outcomeOfClose(1001, 1, 5)).to.equal("stopped");
    expect(outcomeOfClose(1006, 1, 5)).to.equal("retry");
    expect(outcomeOfClose(1006, 5, 5)).to.equal("stopped");
    expect([1, 2, 3, 4, 5, 6].map(retryDelay)).to.deep.equal([500, 1000, 2000, 4000, 8000, 8000]);
  });
});

describe("connection", () => {
  it("reaches stopped after the retries run out, and no further", async () => {
    const phases: string[] = [];
    const { deps, sockets, timers } = fakeDeps(() => Promise.resolve("tk"));
    const connection = createConnection(
      { onPhase: (p) => phases.push(p), onEvent: () => {}, onOpen: () => {} },
      deps,
    );
    connection.start();
    await settle();
    sockets[0].onopen?.();
    for (let i = 0; i < 5; i += 1) {
      sockets[sockets.length - 1].onclose?.({ code: 1006 });
      const timer = timers.pop();
      if (timer) {
        timer();
        await settle();
      }
    }
    expect(phases[phases.length - 1]).to.equal("stopped");
    expect(sockets).to.have.length(5);
    expect(timers).to.have.length(0);
    connection.dispose();
  });

  it("offers a take over on 4409 and reconnects with take_over=1", async () => {
    const phases: string[] = [];
    const { deps, sockets } = fakeDeps(() => Promise.resolve("tk"));
    const connection = createConnection(
      { onPhase: (p) => phases.push(p), onEvent: () => {}, onOpen: () => {} },
      deps,
    );
    connection.start();
    await settle();
    sockets[0].onclose?.({ code: 4409 });
    expect(phases[phases.length - 1]).to.equal("taken");
    connection.takeOver();
    await settle();
    expect(sockets[1].url).to.include("take_over=1");
    const opened: boolean[] = [];
    connection.dispose();
    expect(opened).to.deep.equal([]);
  });

  it("reports not ready when the ticket route answers 503", async () => {
    const phases: string[] = [];
    const { deps, sockets } = fakeDeps(() => Promise.reject(new ApiError(503, "not ready")));
    const connection = createConnection(
      { onPhase: (p) => phases.push(p), onEvent: () => {}, onOpen: () => {} },
      deps,
    );
    connection.start();
    await settle();
    expect(phases).to.deep.equal(["connecting", "not_ready"]);
    expect(sockets).to.have.length(0);
    connection.dispose();
  });
});
