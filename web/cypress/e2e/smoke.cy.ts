// Proves the static export loads with the design tokens applied. The API is
// stubbed: the preview server only serves files.
describe("static export", () => {
  it("serves the Aldwin page with the design tokens applied", () => {
    cy.intercept("GET", "/v1/setup/status", { fixture: "status-clear.json" });
    cy.visit("/");
    cy.get('[data-testid="aldwin-gates-clear"]').should("be.visible").and("contain.text", "Aldwin");
    cy.get("html").should("have.class", "dark");
    cy.get("body").should("have.css", "background-color", "rgb(13, 17, 23)");
  });
});
