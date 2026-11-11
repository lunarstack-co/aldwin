// The first-run gates against stubbed setup routes. Full journeys with a real
// `aldwin web` behind them belong to the release smoke suite.
type Status = {
  keys_ok: boolean;
  host_ready: boolean;
  profile_done: boolean;
  profile_skipped: boolean;
  profile: { first_name: string; occupation: string; personality: string[] };
  model: string;
  provider: string;
  is_local: boolean;
};

const fresh: Status = {
  keys_ok: false,
  host_ready: false,
  profile_done: false,
  profile_skipped: false,
  profile: { first_name: "", occupation: "", personality: [] },
  model: "openai/gpt-4o",
  provider: "openai",
  is_local: false,
};

function statusSequence(...states: Status[]) {
  let call = 0;
  cy.intercept("GET", "/v1/setup/status", (req) => {
    req.reply(states[Math.min(call, states.length - 1)]);
    call += 1;
  }).as("status");
}

describe("onboarding gates", () => {
  it("sends a home without keys to Connect, whatever page it opens", () => {
    statusSequence(fresh);
    cy.visit("/onboarding/personality/");
    cy.location("pathname").should("eq", "/onboarding/connect/");
    cy.contains("h1", "How should Aldwin think?");
    cy.get('[data-testid="aldwin-gates-clear"]').should("not.exist");
  });

  it("checks the key, waits for Aldwin to start, then asks about you", () => {
    const keyed = { ...fresh, keys_ok: true };
    statusSequence(fresh, keyed, { ...keyed, host_ready: true });
    cy.intercept("GET", "/v1/settings", {
      keys: [{ kind: "provider", name: "openai", title: "OpenAI", source: "missing", masked: "" }],
    });
    cy.intercept("POST", "/v1/setup/provider-key", (req) => {
      expect(req.body).to.deep.equal({ name: "openai", key: "sk-test-123456" });
      req.reply({ ok: true, kind: "provider", name: "openai", masked: "sk-…3456" });
    }).as("key");

    cy.visit("/");
    cy.location("pathname").should("eq", "/onboarding/connect/");
    cy.get("#api-key").type("sk-test-123456");
    cy.contains("button", "Check and save").click();
    cy.wait("@key");
    cy.location("pathname").should("eq", "/onboarding/name/");
    cy.get("#api-key").should("not.exist");
  });

  it("shows why a key was refused and stays on Connect", () => {
    statusSequence(fresh);
    cy.intercept("POST", "/v1/setup/provider-key", {
      statusCode: 400,
      body: { detail: "Your OpenAI key was rejected. Check the key and try again." },
    });
    cy.visit("/onboarding/connect/");
    cy.get("#api-key").type("sk-bad-123456");
    cy.contains("button", "Check and save").click();
    cy.get('[role="alert"]').should("contain.text", "was rejected");
    cy.location("pathname").should("eq", "/onboarding/connect/");
  });

  it("lets the profile be skipped from its first page", () => {
    const keyed = { ...fresh, keys_ok: true, host_ready: true };
    statusSequence(keyed, { ...keyed, profile_done: true, profile_skipped: true });
    cy.intercept("POST", "/v1/setup/profile", (req) => {
      expect(req.body).to.deep.equal({ skipped: true });
      req.reply({ ...keyed, profile_done: true, profile_skipped: true });
    }).as("skipProfile");

    cy.visit("/");
    cy.location("pathname").should("eq", "/onboarding/name/");
    cy.contains("button", "Skip for now").click();
    cy.wait("@skipProfile");
    cy.get('[data-testid="aldwin-gates-clear"]').should("be.visible");
  });

  it("sends picked answer styles as a list when finishing", () => {
    const keyed = { ...fresh, keys_ok: true, host_ready: true };
    statusSequence(keyed, { ...keyed, profile_done: true });
    cy.intercept("POST", "/v1/setup/profile", (req) => {
      expect(req.body).to.deep.equal({ personality: ["concise", "teaching"], complete: true });
      req.reply({ ...keyed, profile_done: true });
    }).as("finish");

    cy.visit("/onboarding/personality/");
    cy.contains("button", "Concise").click().should("have.attr", "aria-pressed", "true");
    cy.contains("button", "Teaching").click();
    cy.contains("button", "Finish").click();
    cy.wait("@finish");
    cy.get('[data-testid="aldwin-gates-clear"]').should("be.visible");
  });

  it("leaves onboarding for Aldwin once both gates are clear", () => {
    statusSequence({ ...fresh, keys_ok: true, host_ready: true, profile_done: true });
    cy.visit("/onboarding/connect/");
    cy.location("pathname").should("eq", "/");
    cy.get('[data-testid="aldwin-gates-clear"]').should("be.visible").and("contain.text", "Ask Aldwin anything");
  });
});
