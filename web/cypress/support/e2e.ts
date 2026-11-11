import { MockSocket, type Refusal } from "./chat-ws-mock";

export type ChatVisitOptions = {
  status?: Partial<GatesStatus>;
  reducedMotion?: boolean;
  /** Close every socket with this code instead of opening it. */
  refuseWith?: Refusal;
  /** Answer the ticket route as a server with no host yet. */
  hostNotReady?: boolean;
};

export type GatesStatus = {
  keys_ok: boolean;
  host_ready: boolean;
  profile_done: boolean;
  profile_skipped: boolean;
  profile: { first_name: string; occupation: string; personality: string[] };
  model: string;
  provider: string;
  is_local: boolean;
};

const CLEAR: GatesStatus = {
  keys_ok: true,
  host_ready: true,
  profile_done: true,
  profile_skipped: false,
  profile: { first_name: "Ada", occupation: "", personality: [] },
  model: "openai/gpt-4o",
  provider: "openai",
  is_local: false,
};

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Cypress {
    interface Chainable {
      /** Open the chat with the gates clear, a ticket on offer, and the mock socket installed. */
      visitChat(options?: ChatVisitOptions): Chainable<Cypress.AUTWindow>;
      /** The most recently opened mock socket. */
      socket(): Chainable<MockSocket>;
    }
  }
  interface Window {
    __mockWs: typeof MockSocket;
  }
}

Cypress.Commands.add("visitChat", (options: ChatVisitOptions = {}) => {
  MockSocket.reset();
  MockSocket.refuseWith = options.refuseWith ?? null;
  cy.intercept("GET", "/v1/setup/status", { ...CLEAR, ...options.status }).as("status");
  if (options.hostNotReady) {
    cy.intercept("POST", "/v1/ws-ticket", {
      statusCode: 503,
      body: { detail: "Aldwin is not ready yet. Finish setup, then try again.", error: "host_not_ready" },
    }).as("ticket");
  } else {
    cy.intercept("POST", "/v1/ws-ticket", { ticket: "t-1", expires_in: 30 }).as("ticket");
  }
  return cy.visit("/", {
    onBeforeLoad(win) {
      win.WebSocket = MockSocket as unknown as typeof WebSocket;
      win.__mockWs = MockSocket;
      if (options.reducedMotion) {
        const original = win.matchMedia.bind(win);
        cy.stub(win, "matchMedia").callsFake((query: string) => {
          if (!query.includes("prefers-reduced-motion")) return original(query);
          return {
            matches: true,
            media: query,
            onchange: null,
            addEventListener() {},
            removeEventListener() {},
            addListener() {},
            removeListener() {},
            dispatchEvent: () => false,
          } as MediaQueryList;
        });
      }
    },
  });
});

Cypress.Commands.add("socket", () => cy.window().then((win) => win.__mockWs.last()));
