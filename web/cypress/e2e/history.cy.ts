// The history rail, See all and New chat against a scripted host. The mock
// lists conversations the way the host does: zero-turn ones never appear.
import type { Frame, MockSocket, SessionRow } from "../support/chat-ws-mock";

const ROW = '[data-testid="history-row"]';
const SEE_ALL = '[data-testid="see-all"]';
const SEE_ALL_VIEW = '[data-testid="history-see-all"]';
const SEE_ALL_ROW = '[data-testid="see-all-row"]';
const NEW_CHAT = '[data-testid="new-chat"]';
const INPUT = '[data-testid="composer-input"]';
const NOTICE = '[data-testid="session-notice"]';

const HOUR = 3_600_000;

function row(id: string, title: string, updatedAt: string): SessionRow {
  return { id, title, created_at: updatedAt, updated_at: updatedAt, model: "openai/gpt-4o", interrupted: false };
}

function today(): string {
  return new Date().toISOString();
}

function daysAgo(days: number): string {
  return new Date(Date.now() - days * 24 * HOUR).toISOString();
}

/** A zero-turn conversation: open, but never in the list. */
const EMPTY = row("z", "", today());

function phase(expected: string) {
  cy.get('[data-testid="connection-status"]').should("have.attr", "data-phase", expected);
}

function sent(socket: MockSocket, type: string): Frame[] {
  return socket.sentOfType(type);
}

function types(socket: MockSocket): string[] {
  return socket.sent.map((frame) => frame.type);
}

function send(text: string) {
  cy.get(INPUT).type(`${text}{enter}`);
}

/** Script the host to stream `chunks` for the next turn, ending it unless `keepOpen`. */
function hostAnswers(chunks: string[], keepOpen = false) {
  cy.window().then((win) => {
    win.__mockWs.onTurn = (socket, frame) => {
      for (const delta of chunks) socket.emit({ re: frame.id, kind: "text", delta });
      if (!keepOpen) {
        socket.emit({ re: frame.id, kind: "done", reason: "answered" });
        socket.activeTurn = null;
      }
    };
  });
}

function setHistory(rows: { role: "user" | "assistant" | "tool"; text: string }[]) {
  cy.window().then((win) => {
    win.__mockWs.history = rows;
  });
}

describe("history", () => {
  const trip = row("a", "Trip to Lisbon", today());
  const budget = row("b", "Lisbon budget", today());
  const tax = row("c", "Tax notes", daysAgo(3));
  const untitled = row("d", "", daysAgo(5));

  beforeEach(() => {
    cy.intercept("/v1/conversations*").as("rest");
  });

  it("lists only conversations with turns and marks none while the open one is empty", () => {
    cy.visitChat({ sessions: { items: [trip, untitled], current: EMPTY } });
    phase("open");
    cy.get('[data-testid="history-eyebrow"]').invoke("text").should("match", /^history$/i);
    cy.get(ROW).should("have.length", 2);
    cy.get(ROW).eq(1).should("have.text", "Untitled chat");
    cy.get(`${ROW}[aria-current]`).should("not.exist");
    cy.get('[data-testid="empty-home"]').should("be.visible");
    cy.get("nav").should("not.contain.text", "Imagine").and("not.contain.text", "Files");
  });

  it("marks the open conversation and opens another from the rail over the socket", () => {
    cy.visitChat({
      sessions: { items: [trip, budget], current: trip },
      history: [{ role: "user", text: "Plan Lisbon" }, { role: "assistant", text: "Day one: Alfama." }],
    });
    phase("open");
    cy.get(ROW).first().should("have.attr", "aria-current", "page");
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Alfama");

    setHistory([{ role: "user", text: "How much?" }, { role: "assistant", text: "About 900 euros." }]);
    cy.get(ROW).eq(1).click();
    cy.get('[data-testid="assistant-message"]').should("contain.text", "900 euros");
    cy.get('[data-testid="user-message"]').should("have.length", 1).and("contain.text", "How much?");
    cy.get(ROW).eq(1).should("have.attr", "aria-current", "page");
    cy.get(ROW).first().should("not.have.attr", "aria-current");
    cy.socket().then((socket) => {
      expect(sent(socket, "sessions.open")).to.have.length(1);
      expect(sent(socket, "sessions.open")[0].session_id).to.equal("b");
      const after = types(socket).slice(types(socket).indexOf("sessions.open"));
      expect(after.slice(0, 3)).to.deep.equal(["sessions.open", "history", "sessions.list"]);
    });
    cy.get("@rest.all").should("have.length", 0);
  });

  it("opens a rail row from the keyboard the same way", () => {
    cy.visitChat({ sessions: { items: [trip, budget], current: trip } });
    phase("open");
    // A real button takes focus and activates; the live browser pass presses the keys.
    cy.get(ROW).eq(1).focus();
    cy.focused().should("have.attr", "data-testid", "history-row").and("match", "button").click();
    cy.socket().then((socket) => expect(sent(socket, "sessions.open")[0].session_id).to.equal("b"));
    cy.get(ROW).eq(1).should("have.attr", "aria-current", "page");
  });

  it("keeps the rail to the 15 most recent and the rest in See all", () => {
    const many = Array.from({ length: 16 }, (_, i) => row(`m${i + 1}`, `Chat ${i + 1}`, daysAgo(i)));
    cy.visitChat({ sessions: { items: many, current: EMPTY } });
    phase("open");
    cy.get(ROW).should("have.length", 15).last().should("have.text", "Chat 15");
    cy.get("nav").should("not.contain.text", "Chat 16");
    cy.get(SEE_ALL).click();
    cy.get(SEE_ALL_ROW).should("have.length", 16);
    cy.get(SEE_ALL_VIEW).should("contain.text", "Chat 16");

    // An old conversation opened from See all keeps a rail row, so the rail always marks the open one.
    cy.get(SEE_ALL_ROW).contains("Chat 16").click();
    cy.get(SEE_ALL_VIEW).should("not.exist");
    cy.get(ROW).should("have.length", 16).last().should("have.text", "Chat 16").and("have.attr", "aria-current", "page");
  });

  it("opens See all inside the shell, grouped by day and searchable", () => {
    cy.visitChat({ sessions: { items: [trip, budget, tax, untitled], current: EMPTY } });
    phase("open");
    cy.socket().then((socket) => cy.wrap(sent(socket, "sessions.list").length).as("listsBefore"));
    cy.get(SEE_ALL).click();
    cy.location("pathname").should("eq", "/");
    cy.get(SEE_ALL_VIEW).should("be.visible").and("contain.text", "All conversations");
    cy.get('[data-testid="composer"]').should("not.exist");
    cy.get(SEE_ALL).should("have.attr", "aria-current", "page");
    cy.get("@listsBefore").then((before) => {
      cy.socket().then((socket) => expect(sent(socket, "sessions.list").length).to.be.greaterThan(Number(before)));
    });

    cy.get('[data-testid="history-group-today"]').within(() => {
      cy.contains("h2", /today/i);
      cy.get(SEE_ALL_ROW).should("have.length", 2);
    });
    cy.get('[data-testid="history-group-earlier"]').within(() => {
      cy.contains("h2", /earlier/i);
      cy.get(SEE_ALL_ROW).should("have.length", 2).last().should("have.text", "Untitled chat");
    });

    cy.get('[data-testid="history-search"]').should("be.focused").type("LISBON");
    cy.get(SEE_ALL_ROW).should("have.length", 2);
    cy.get('[data-testid="history-group-earlier"]').should("not.exist");
    cy.get('[data-testid="history-search"]').clear().type("untitled");
    cy.get(SEE_ALL_ROW).should("have.length", 1).and("have.text", "Untitled chat");
    cy.get('[data-testid="history-search"]').clear().type("zzz");
    cy.get(SEE_ALL_ROW).should("not.exist");
    cy.get('[data-testid="history-empty"]').should("have.text", 'No conversations match "zzz".');
  });

  it("opens a conversation from See all and leaves See all with Esc or Back", () => {
    cy.visitChat({ sessions: { items: [trip, tax], current: EMPTY } });
    phase("open");
    setHistory([{ role: "user", text: "Receipts?" }, { role: "assistant", text: "Keep six years." }]);
    cy.get(SEE_ALL).click();
    cy.get(SEE_ALL_ROW).contains("Tax notes").click();
    cy.get(SEE_ALL_VIEW).should("not.exist");
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Keep six years.");
    cy.socket().then((socket) => expect(sent(socket, "sessions.open")[0].session_id).to.equal("c"));

    cy.get(SEE_ALL).click();
    cy.get(SEE_ALL_VIEW).should("be.visible");
    cy.get("body").type("{esc}");
    cy.get(SEE_ALL_VIEW).should("not.exist");
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Keep six years.");

    cy.get(SEE_ALL).click();
    cy.get('[data-testid="see-all-back"]').click();
    cy.get(SEE_ALL_VIEW).should("not.exist");
    cy.get('[data-testid="composer"]').should("be.visible");

    // The open conversation's own row just goes back to it.
    cy.get(SEE_ALL).click();
    cy.get(`${SEE_ALL_ROW}[aria-current="page"]`).click();
    cy.get(SEE_ALL_VIEW).should("not.exist");
    cy.socket().then((socket) => expect(sent(socket, "sessions.open")).to.have.length(1));
  });

  it("New chat on an empty conversation reuses it and asks the host for nothing", () => {
    cy.visitChat({ sessions: { items: [trip], current: EMPTY } });
    phase("open");
    cy.get(NEW_CHAT).should("be.disabled");
    cy.get(SEE_ALL).click();
    cy.get(NEW_CHAT).should("be.enabled").click();
    cy.get(SEE_ALL_VIEW).should("not.exist");
    cy.get('[data-testid="empty-home"]').should("be.visible");
    cy.get(NEW_CHAT).should("be.disabled");
    cy.socket().then((socket) => expect(sent(socket, "sessions.new")).to.have.length(0));
  });

  it("New chat after a turn asks the host once and lands on the empty home", () => {
    cy.visitChat({
      sessions: { items: [trip], current: trip },
      history: [{ role: "user", text: "Plan Lisbon" }, { role: "assistant", text: "Day one." }],
    });
    phase("open");
    cy.get(NEW_CHAT).should("be.enabled").click();
    cy.get('[data-testid="empty-home"]').should("be.visible");
    cy.get('[data-testid="turn"]').should("not.exist");
    cy.get(ROW).should("have.length", 1).and("contain.text", "Trip to Lisbon").and("not.have.attr", "aria-current");
    cy.get(NEW_CHAT).should("be.disabled");
    cy.socket().then((socket) => expect(sent(socket, "sessions.new")).to.have.length(1));
  });

  it("says busy when switching mid-answer, and leaves the answer running", () => {
    cy.visitChat({ sessions: { items: [trip, budget], current: trip } });
    phase("open");
    hostAnswers(["Thinking"], true);
    send("one more thing");

    cy.get(ROW).eq(1).should("be.enabled").click();
    cy.get(NOTICE).should("have.attr", "data-notice", "busy").and("contain.text", "Stop it before switching");
    cy.get(ROW).first().should("have.attr", "aria-current", "page");
    cy.get('[data-testid="user-message"]').should("contain.text", "one more thing");
    cy.get('[data-testid="composer-stop"]').should("be.visible");

    cy.get(NEW_CHAT).should("be.enabled").click();
    cy.get(NOTICE).should("have.attr", "data-notice", "busy");
    cy.get('[data-testid="user-message"]').should("contain.text", "one more thing");
    cy.socket().then((socket) => {
      expect(sent(socket, "sessions.open")).to.have.length(1);
      expect(sent(socket, "sessions.new")).to.have.length(1);
      expect(sent(socket, "stop")).to.have.length(0);
      const turn = sent(socket, "turn")[0];
      socket.emit({ re: turn.id, kind: "text", delta: " done." });
      socket.emit({ re: turn.id, kind: "done", reason: "answered" });
      socket.activeTurn = null;
    });
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Thinking done.");
    cy.get(NOTICE).should("not.exist");
  });

  it("stays put and refreshes the list when a conversation was deleted elsewhere", () => {
    cy.visitChat({
      sessions: { items: [trip, budget], current: trip },
      history: [{ role: "user", text: "Plan Lisbon" }, { role: "assistant", text: "Day one." }],
    });
    phase("open");
    cy.window().then((win) => {
      win.__mockWs.sessions = win.__mockWs.sessions.filter((item) => item.id !== "b");
    });
    cy.get(ROW).eq(1).click();
    cy.get(NOTICE).should("have.attr", "data-notice", "not_found");
    cy.get(ROW).should("have.length", 1).first().should("have.attr", "aria-current", "page");
    cy.get('[data-testid="assistant-message"]').should("contain.text", "Day one.");
    cy.get(`${NOTICE} button`).click();
    cy.get(NOTICE).should("not.exist");
  });

  it("shows the host's own words when a switch fails for another reason", () => {
    cy.visitChat({ sessions: { items: [trip, budget], current: trip } });
    phase("open");
    cy.window().then((win) => {
      win.__mockWs.openError = { code: "internal", message: "Aldwin couldn't read that conversation." };
    });
    cy.get(ROW).eq(1).click();
    cy.get(NOTICE).should("have.attr", "data-notice", "other").and("contain.text", "couldn't read that conversation");
    cy.get(ROW).first().should("have.attr", "aria-current", "page");
  });

  it("lists a new conversation under its title once its first turn is done", () => {
    cy.visitChat({ sessions: { items: [], current: null } });
    phase("open");
    cy.contains("No conversations yet");
    hostAnswers(["Hi."]);
    send("Weekend in Porto");
    cy.get(ROW).should("have.length", 1).and("have.text", "Weekend in Porto").and("have.attr", "aria-current", "page");
  });

  it("cannot open or start conversations while the connection is down", () => {
    cy.clock(Date.now(), ["setTimeout", "clearTimeout", "Date"]);
    cy.visitChat({ sessions: { items: [trip, budget], current: trip } });
    phase("open");
    cy.window().then((win) => {
      win.__mockWs.refuseWith = { code: 1006, reason: "" };
    });
    cy.socket().then((socket) => socket.drop(1006));
    phase("reconnecting");
    cy.get(ROW).each((el) => cy.wrap(el).should("be.disabled"));
    cy.get(SEE_ALL).should("be.disabled");
    cy.get(NEW_CHAT).should("be.disabled");

    // Every reconnect is refused, so the page gives up and ends on the stopped screen.
    // Each retry mints a ticket before the next timer exists, so the clock moves one step at a time.
    for (const [delay, tickets] of [[500, 2], [1000, 3], [2000, 4], [4000, 5]] as const) {
      cy.tick(delay);
      cy.get("@ticket.all").should("have.length", tickets);
    }
    cy.get('[data-testid="aldwin-stopped"]').should("be.visible");
    cy.get(ROW).each((el) => cy.wrap(el).should("be.disabled"));
    cy.socket().then((socket) => {
      expect(sent(socket, "sessions.open")).to.have.length(0);
      expect(sent(socket, "sessions.new")).to.have.length(0);
    });
  });

  it("switches the theme with history listed and never tells the server", () => {
    cy.intercept("POST", "/v1/setup/save").as("save");
    cy.visitChat({ sessions: { items: [trip, tax], current: trip } });
    phase("open");
    cy.get('[role="radio"][aria-label="Dark"]').click();
    cy.get("html").should("have.class", "dark");
    cy.get(ROW).should("have.length", 2);
    cy.get("@save.all").should("have.length", 0);
  });
});
