// The chat shell against a scripted host. The socket is a mock installed
// before the page loads; each test plays the host's side of the protocol.
import { streamAnswer, type Frame, type MockSocket } from "../support/chat-ws-mock";

const SHELL = '[data-testid="aldwin-chat-shell"]';
const STATUS = '[data-testid="connection-status"]';
const INPUT = '[data-testid="composer-input"]';
const SEND = '[data-testid="composer-send"]';
const STOP = '[data-testid="composer-stop"]';
const TURN = '[data-testid="turn"]';

function phase(expected: string) {
  cy.get(STATUS).should("have.attr", "data-phase", expected);
}

function sent(socket: MockSocket, type: string): Frame[] {
  return socket.sentOfType(type);
}

/** Type a message and press Enter. */
function send(text: string) {
  cy.get(INPUT).type(`${text}{enter}`);
}

/** Script the host to stream `chunks` for the next turn, ending it unless `keepOpen`. */
function hostAnswers(chunks: string[], keepOpen = false, tool?: { name: string; ok: boolean }) {
  cy.window().then((win) => {
    win.__mockWs.onTurn = (socket, frame) => {
      if (tool) {
        socket.emit({ re: frame.id, kind: "tool_call", call_id: "c1", name: tool.name, args_preview: "pattern=needle", summary: "" });
        socket.emit({ re: frame.id, kind: "tool_result", call_id: "c1", ok: tool.ok, summary: "3 rows", preview: "", duration_ms: 120 });
      }
      for (const delta of chunks) socket.emit({ re: frame.id, kind: "text", delta });
      if (!keepOpen) {
        socket.emit({ re: frame.id, kind: "done", reason: "answered" });
        socket.activeTurn = null;
      }
    };
  });
}

describe("chat shell", () => {
  it("lands on the empty home once the gates are clear", () => {
    cy.visitChat();
    phase("open");
    cy.get(SHELL).should("be.visible");
    cy.get('[data-testid="aldwin-gates-clear"]').should("be.visible");
    cy.get('[data-testid="empty-home"]').contains("h1", "Ask Aldwin anything");
    cy.get('[data-testid="composer"]').should("have.attr", "data-variant", "centered");
    cy.contains("Local · no uploads · no scheduler");
    cy.get('input[type="file"]').should("not.exist");
    cy.get('[data-testid="composer-model"]').should("contain.text", "openai/gpt-4o");
    cy.get('[data-testid="composer-send"]').should("have.css", "background-color", "rgb(56, 189, 176)");
    cy.get("@ticket.all").should("have.length", 1);
    cy.socket().then((socket) => {
      expect(socket.sent.map((f) => f.type)).to.deep.equal(["hello", "history", "sessions.list"]);
      expect(socket.url).to.match(/^ws:\/\/127\.0\.0\.1:4173\/v1\/ws\?ticket=t-1$/);
    });
    cy.get("head title").should("have.text", "Aldwin");
    cy.get('head link[rel="icon"]').should("exist");
  });

  it("shows the loading splash before the status arrives", () => {
    cy.intercept("GET", "/v1/setup/status", (req) => {
      req.reply({ delay: 600, fixture: "status-clear.json" });
    });
    cy.intercept("POST", "/v1/ws-ticket", { ticket: "t-1", expires_in: 30 });
    cy.visit("/");
    cy.get('[data-testid="aldwin-gates-loading"]').should("have.attr", "role", "status").and("contain.text", "Loading");
    cy.get(SHELL).should("be.visible");
  });

  it("keeps a home without keys on the setup pages", () => {
    cy.visitChat({ status: { keys_ok: false, host_ready: false } });
    cy.location("pathname").should("eq", "/onboarding/connect/");
    cy.get(SHELL).should("not.exist");
    cy.get("@ticket.all").should("have.length", 0);
  });

  it("switches the theme in the browser only", () => {
    cy.intercept("POST", "/v1/setup/save").as("save");
    cy.visitChat();
    phase("open");
    cy.get("html").should("have.class", "dark");
    cy.get('[role="radio"][aria-label="Light"]').click();
    cy.get("html").should("not.have.class", "dark").and("have.class", "light");
    cy.window().then((win) => expect(win.localStorage.getItem("theme")).to.equal("light"));
    cy.get('[role="radio"][aria-label="Dark"]').click();
    cy.get("html").should("have.class", "dark");
    cy.get('[role="radio"][aria-label="System"]').click();
    cy.get('[role="radio"][aria-label="System"]').should("have.attr", "aria-checked", "true");
    cy.window().then((win) => {
      const dark = win.matchMedia("(prefers-color-scheme: dark)").matches;
      expect(win.document.documentElement.classList.contains("dark")).to.equal(dark);
      expect(win.localStorage.getItem("theme")).to.equal("system");
    });
    cy.get("@save.all").should("have.length", 0);
  });

  it("sends a message, docks the composer and offers Stop while the answer streams", () => {
    cy.visitChat();
    phase("open");
    hostAnswers(["Hello"], true, { name: "read_file", ok: true });
    send("hi there");
    cy.get('[data-testid="user-message"]').should("contain.text", "hi there");
    cy.get('[data-testid="composer"]').should("have.attr", "data-variant", "docked");
    cy.get(STOP).should("be.visible").and("contain.text", "Stop");
    cy.get('[data-testid="composer"] [role="status"]').invoke("text").should("match", /Working for \d+s/);
    cy.get('[data-testid="tool-group"]').should("have.attr", "data-working", "true").and("have.attr", "data-open", "false");
    cy.get('[data-testid="tool-card"]').should("not.exist");
    cy.get('[data-testid="assistant-message"]').should("have.attr", "aria-busy", "true").and("not.have.attr", "aria-live");
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Hello");
    cy.get('[data-testid="composer"] [role="status"]').should("have.attr", "aria-live", "polite");
    cy.get('[data-testid="new-chat"]').should("be.disabled");
    cy.socket().then((socket) => {
      const turn = sent(socket, "turn")[0];
      expect(turn.text).to.equal("hi there");
      expect(turn.skills).to.deep.equal([]);
      streamAnswer(socket, turn.id, [" there"]);
    });
    cy.get(TURN).should("have.attr", "data-status", "complete");
    cy.get('[data-testid="tool-group"]').invoke("text").should("match", /1 tool call in \d+s/);
    cy.get('[data-testid="message-actions"]').should("be.visible");
    cy.get(SEND).should("be.visible");
    cy.get('[data-testid="history-row"]').should("have.length", 1).and("contain.text", "hi there").and("have.attr", "aria-current", "page");
  });

  it("stops a turn from the pointer and from the keyboard", () => {
    cy.visitChat();
    phase("open");
    hostAnswers(["Thinking"], true);
    send("one");
    cy.get(STOP).click();
    cy.socket().then((socket) => expect(sent(socket, "stop")).to.have.length(1));
    cy.get(TURN).last().should("have.attr", "data-status", "incomplete");
    cy.contains("Stopped before the answer was finished.");
    cy.get(INPUT).should("be.enabled");

    hostAnswers(["Again"], true);
    send("two");
    // Cypress cannot press Enter the way a keyboard does; the control is a real
    // button, so proving it takes focus and activating the focused element is
    // the closest an automated check gets. The live Playwright pass presses keys.
    cy.get(STOP).focus();
    cy.focused().should("have.attr", "data-testid", "composer-stop").and("match", "button").click();
    cy.socket().then((socket) => expect(sent(socket, "stop")).to.have.length(2));
    cy.get(TURN).should("have.length", 2).last().should("have.attr", "data-status", "incomplete");
  });

  it("keeps tool calls folded until Detail is chosen, and remembers the choice", () => {
    cy.visitChat();
    phase("open");
    hostAnswers(["Done."], false, { name: "grep", ok: true });
    send("find it");
    cy.get('[data-testid="tool-group"]').should("have.attr", "data-open", "false");
    cy.get('[data-testid="tool-card"]').should("not.exist");
    cy.get('[role="radio"]').contains("Clean").should("have.attr", "aria-checked", "true");
    // The visible count and timing are the button's accessible name.
    cy.get('[data-testid="tool-group"] button').first().should("not.have.attr", "aria-label");
    cy.get('[data-testid="tool-group"] button').first().should("have.attr", "aria-expanded", "false");
    cy.get('[data-testid="tool-group"] button').first().invoke("text").should("match", /1 tool call in \d+s/);
    cy.get('[data-testid="tool-group"] button').first().click();
    cy.get('[data-testid="tool-card"]').should("be.visible").and("have.attr", "data-state", "ok").and("contain.text", "grep");
    cy.get('[data-testid="tool-card"] button').should("not.have.attr", "aria-label");
    cy.get('[data-testid="tool-card"] button').click();
    cy.get('[data-testid="tool-card"]').should("contain.text", "pattern=needle").and("contain.text", "3 rows");
    cy.get('[data-testid="tool-group"] button').first().click();
    cy.get('[data-testid="tool-card"]').should("not.exist");

    cy.get('[role="radio"]').contains("Detail").click();
    cy.window().then((win) => expect(win.localStorage.getItem("aldwin.density")).to.equal("detail"));

    cy.visitChat();
    phase("open");
    hostAnswers(["Done."], false, { name: "grep", ok: false });
    send("find it again");
    cy.get('[role="radio"]').contains("Detail").should("have.attr", "aria-checked", "true");
    cy.get('[data-testid="tool-card"]').should("be.visible").and("have.attr", "data-state", "failed");
  });

  it("copies the answer and retries as a new turn, by pointer and by keyboard", () => {
    cy.visitChat();
    phase("open");
    cy.window().then((win) => {
      cy.stub(win.navigator.clipboard, "writeText").as("write").resolves();
    });
    hostAnswers(["The answer."]);
    send("question");
    cy.get('[data-testid="copy-answer"]').click();
    cy.get("@write").should("have.been.calledWith", "The answer.");
    cy.get('[data-testid="copy-answer"] [role="status"]').should("contain.text", "Copied");

    cy.get('[data-testid="retry-turn"]').click();
    cy.get(TURN).should("have.length", 2);
    cy.get('[data-testid="user-message"]').last().should("contain.text", "question");
    cy.socket().then((socket) => {
      const turns = sent(socket, "turn");
      expect(turns).to.have.length(2);
      expect(turns[1].text).to.equal("question");
      expect(turns[1].id).to.not.equal(turns[0].id);
    });

    cy.get('[data-testid="copy-answer"]').last().focus();
    cy.focused().should("have.attr", "data-testid", "copy-answer").and("match", "button").click();
    cy.get("@write").should("have.been.calledTwice");
    cy.get('[data-testid="retry-turn"]').last().focus();
    cy.focused().should("have.attr", "data-testid", "retry-turn").and("match", "button").click();
    cy.get(TURN).should("have.length", 3);
  });

  it("shows a failed turn as an error with Retry and Copy error", () => {
    cy.visitChat();
    phase("open");
    cy.window().then((win) => {
      cy.stub(win.navigator.clipboard, "writeText").as("write").resolves();
      win.__mockWs.onTurn = (socket, frame) => {
        socket.emit({ re: frame.id, kind: "error", code: "busy", message: "Still working on your last request." });
      };
    });
    send("fails");
    cy.get('[data-testid="error-row"]').should("have.attr", "role", "alert").and("contain.text", "Still working");
    cy.get('[data-testid="error-row"] p').should("have.class", "text-danger");
    cy.get('[data-testid="copy-error"]').click();
    cy.get("@write").should("have.been.calledWith", "Still working on your last request.");
    hostAnswers(["Better."]);
    cy.get('[data-testid="retry-error"]').click();
    cy.get(TURN).should("have.length", 2).last().should("have.attr", "data-status", "complete");
  });

  it("renders hostile markdown without running any of it", () => {
    cy.visitChat();
    phase("open");
    cy.fixture("hostile-markdown.md").then((hostile: string) => hostAnswers([hostile]));
    send("show me");
    cy.get('[data-testid="assistant-message"]').within(() => {
      cy.contains("strong", "bold");
      cy.get("script, iframe, img, div[onclick]").should("not.exist");
      cy.get('a[href^="javascript:"]').should("not.exist");
      cy.get('a[href="https://example.com/page"]')
        .should("have.attr", "rel")
        .and("include", "noopener")
        .and("include", "noreferrer");
    });
    cy.window().its("__xss").should("be.undefined");
    cy.get('[data-testid="assistant-message"]').invoke("html").should("not.include", "<script");
  });

  it("catches up after a drop mid-stream with the partial text and history", () => {
    cy.visitChat();
    phase("open");
    hostAnswers(["Hello"], true);
    send("hi");
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Hello");
    cy.window().then((win) => {
      win.__mockWs.status = { turn_streamed_chunks: 1, active_tool: null, partial_text: "Hello" };
      win.__mockWs.history = [{ role: "user", text: "hi" }];
      win.__mockWs.last().drop(1006);
    });
    phase("reconnecting");
    cy.get("@ticket.all").should("have.length", 2);
    phase("open");
    cy.socket().then((socket) => {
      expect(socket.url).to.include("ticket=t-1");
      expect(socket.sent.map((f) => f.type)).to.deep.equal(["hello", "status", "history", "sessions.list"]);
      socket.emit({ re: "turn_from_before", kind: "text", delta: " again" });
      socket.emit({ re: "turn_from_before", kind: "done", reason: "answered" });
    });
    cy.get(TURN).should("have.length", 1).and("have.attr", "data-status", "complete");
    cy.get('[data-testid="user-message"]').should("contain.text", "hi");
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Hello again");
  });

  it("ends with Aldwin stopped when the server says goodbye", () => {
    cy.visitChat();
    phase("open");
    cy.socket().then((socket) => socket.drop(1001));
    cy.get('[data-testid="aldwin-stopped"]').should("have.attr", "role", "alert").and("contain.text", "Aldwin stopped");
    cy.get('[data-testid="composer"]').should("not.exist");
    cy.get("@ticket.all").should("have.length", 1);
  });

  it("gives up after a bounded number of reconnects instead of looping", () => {
    cy.clock(Date.now(), ["setTimeout", "clearTimeout", "Date"]);
    cy.visitChat();
    phase("open");
    cy.window().then((win) => {
      win.__mockWs.refuseWith = { code: 1006, reason: "" };
      win.__mockWs.last().drop(1006);
    });
    phase("reconnecting");
    cy.get(STATUS).should("contain.text", "try 1 of 5");
    cy.tick(500);
    cy.get("@ticket.all").should("have.length", 2);
    cy.tick(1000);
    cy.get("@ticket.all").should("have.length", 3);
    cy.tick(2000);
    cy.get("@ticket.all").should("have.length", 4);
    cy.tick(4000);
    cy.get("@ticket.all").should("have.length", 5);
    cy.get('[data-testid="aldwin-stopped"]').should("be.visible");
    cy.tick(60_000);
    cy.get("@ticket.all").should("have.length", 5);
    cy.window().then((win) => expect(win.__mockWs.instances).to.have.length(5));
  });

  it("offers Take over when another tab holds the socket", () => {
    cy.visitChat({ refuseWith: { code: 4409, reason: "already_attached" } });
    cy.get('[data-testid="take-over"]').should("have.attr", "role", "dialog").and("contain.text", "another tab");
    cy.get('[data-testid="take-over-button"]').should("have.focus");
    cy.get("@ticket.all").should("have.length", 1);
    cy.window().then((win) => {
      win.__mockWs.refuseWith = null;
      win.__mockWs.history = [{ role: "user", text: "from the other tab" }, { role: "assistant", text: "Sure." }];
    });
    cy.get('[data-testid="take-over-button"]').click();
    cy.get("@ticket.all").should("have.length", 2);
    phase("open");
    cy.get('[data-testid="take-over"]').should("not.exist");
    cy.socket().then((socket) => {
      expect(socket.url).to.include("take_over=1");
      expect(socket.sent.map((f) => f.type)).to.deep.equal(["hello", "status", "history", "sessions.list"]);
    });
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Sure.");
  });

  it("starts a new chat on the empty home and keeps the old one in the rail", () => {
    cy.visitChat();
    phase("open");
    cy.get('[data-testid="new-chat"]').should("be.disabled");
    hostAnswers(["Yes."]);
    send("first");
    cy.get('[data-testid="history-row"]').should("have.length", 1).and("have.attr", "aria-current", "page");
    cy.get('[data-testid="new-chat"]').should("be.enabled").click();
    cy.socket().then((socket) => expect(sent(socket, "sessions.new")).to.have.length(1));
    cy.get('[data-testid="empty-home"]').should("be.visible");
    cy.get(TURN).should("not.exist");
    cy.get('[data-testid="history-row"]').should("have.length", 1).and("contain.text", "first").and("not.have.attr", "aria-current");
    cy.get('[data-testid="new-chat"]').should("be.disabled");
  });

  it("reopens a saved conversation from the rail", () => {
    cy.visitChat();
    phase("open");
    hostAnswers(["Yes."]);
    send("first");
    cy.get('[data-testid="new-chat"]').click();
    cy.get('[data-testid="empty-home"]').should("be.visible");
    cy.window().then((win) => {
      win.__mockWs.history = [{ role: "user", text: "first" }, { role: "assistant", text: "Yes." }];
    });
    cy.get('[data-testid="history-row"]').first().click();
    cy.socket().then((socket) => expect(sent(socket, "sessions.open")[0].session_id).to.equal("s1"));
    cy.get('[data-testid="user-message"]').should("contain.text", "first");
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Yes.");
    cy.get('[data-testid="history-row"]').first().should("have.attr", "aria-current", "page");

    // The conversation is deleted elsewhere; opening it again drops it from the rail.
    hostAnswers(["Two."]);
    send("second");
    cy.get('[data-testid="new-chat"]').click();
    cy.get('[data-testid="history-row"]').should("have.length", 1);
    cy.window().then((win) => {
      win.__mockWs.sessions = win.__mockWs.sessions.filter((row) => row.id !== "s1");
    });
    cy.get('[data-testid="history-row"]').first().click();
    cy.socket().then((socket) => expect(sent(socket, "sessions.open").at(-1)?.session_id).to.equal("s1"));
    cy.get('[data-testid="history-row"]').should("have.length", 0);
    cy.get('[data-testid="empty-home"]').should("be.visible");
  });

  it("leaves @, See all and Settings inert until their screens exist", () => {
    cy.visitChat();
    phase("open");
    for (const id of ["composer-skills", "see-all", "settings"]) {
      cy.get(`[data-testid="${id}"]`).should("be.disabled").click({ force: true });
    }
    cy.location("pathname").should("eq", "/");
    cy.get('[role="listbox"], [role="menu"], [role="dialog"]').should("not.exist");
    cy.get(SHELL).should("be.visible");
  });

  it("keeps Working and Stop visible with reduced motion", () => {
    cy.visitChat({ reducedMotion: true });
    phase("open");
    hostAnswers(["…"], true, { name: "grep", ok: true });
    send("slow");
    cy.get(STOP).should("be.visible");
    cy.get('[data-testid="composer"] [role="status"]').should("be.visible").invoke("text").should("match", /Working for/);
    cy.get('[data-testid="tool-group"]').should("be.visible");
  });

  it("says so when no host is serving, and connects once one is", () => {
    cy.visitChat({ hostNotReady: true });
    phase("not_ready");
    cy.get(STATUS).should("contain.text", "Not ready yet");
    cy.get(INPUT).should("be.disabled");
    cy.window().then((win) => expect(win.__mockWs.instances).to.have.length(0));
    cy.intercept("POST", "/v1/ws-ticket", { ticket: "t-2", expires_in: 30 }).as("ticket2");
    cy.get('[data-testid="connection-retry"]').click();
    phase("open");
    cy.socket().then((socket) => expect(socket.url).to.include("ticket=t-2"));
    cy.get(INPUT).should("be.enabled");
  });

  it("never answers an approval on the user's behalf", () => {
    cy.visitChat();
    phase("open");
    cy.window().then((win) => {
      win.__mockWs.onTurn = (socket, frame) => {
        socket.emit({ re: frame.id, kind: "approval", approval_id: "a1", tool: "run_command", tier: "ask", summary: "df -h", detail: "", grantable: false, grant_scope: null, digest: "sha256:x", risky: [] });
        socket.emit({ re: frame.id, kind: "text", delta: "Waiting." });
      };
    });
    send("run it");
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Waiting.");
    cy.wait(300);
    cy.socket().then((socket) => expect(sent(socket, "approve")).to.have.length(0));
  });
});
