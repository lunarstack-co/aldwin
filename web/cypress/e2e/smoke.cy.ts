// Proves only that the static export loads with the design tokens applied.
// Journey specs (onboarding, chat, history, @ menu) arrive with those screens.
describe("static export", () => {
  it("serves the Aldwin page with the design tokens applied", () => {
    cy.visit("/");
    cy.get('[data-testid="aldwin-stub"]').should("be.visible").and("contain.text", "Aldwin");
    cy.get("html").should("have.class", "dark");
    cy.get("body").should("have.css", "background-color", "rgb(13, 17, 23)");
  });
});
