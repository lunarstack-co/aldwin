import type { Page } from "@playwright/test";

import { connectorFixture } from "./fixtures/settings-fixtures";
import { expect, openChat, test } from "./fixtures/test";

const input = (page: Page) => page.getByTestId("composer-input");
const menu = (page: Page) => page.getByTestId("apps-menu");
const chips = (page: Page) => page.getByTestId("composer-apps");

async function typeInComposer(page: Page, text: string): Promise<void> {
  await input(page).click();
  await page.keyboard.type(text);
}

function withLinear(host: { connectors: { items: ReturnType<typeof connectorFixture>[] } }) {
  const github = host.connectors.items.find((c) => c.name === "github")!;
  host.connectors.items.push(connectorFixture("linear", "Linear", { tools: github.tools.slice(0, 2) }));
}

test("typing / lists only connected apps, with their marks @cross", async ({ page, host }) => {
  withLinear(host);
  await openChat(page);
  await typeInComposer(page, "/");
  await expect(menu(page).getByRole("option")).toHaveCount(2);
  await expect(menu(page).getByRole("option", { name: /GitHub/ })).toContainText("12 tools");
  await expect(menu(page).getByRole("option", { name: /Linear/ })).toContainText("2 tools");
  await expect(menu(page).locator('svg[data-brand="linear"]')).toBeVisible();
  await expect(menu(page)).not.toContainText("Notion");
  expect(host.sentOfType("connectors.list")).toHaveLength(1);
});

test("typing after / filters the apps", async ({ page, host }) => {
  withLinear(host);
  await openChat(page);
  await typeInComposer(page, "/lin");
  await expect(menu(page).getByRole("option")).toHaveText([/Linear/]);
  // A word no app matches closes the menu, so the text can be sent as it is.
  await typeInComposer(page, "zzz");
  await expect(menu(page)).toBeHidden();
});

test("picking an app makes a chip, clears /, and sends the app with the message @cross", async ({ page, host }) => {
  withLinear(host);
  host.answerNext("Done.");
  await openChat(page);
  await typeInComposer(page, "/lin");
  await page.keyboard.press("Enter");
  await expect(input(page)).toHaveValue("");
  await expect(chips(page).locator('[data-picked-app="linear"]')).toHaveText("Linear");
  await expect(chips(page).locator('svg[data-brand="linear"]')).toBeVisible();
  await typeInComposer(page, "Close the login bug");
  await page.keyboard.press("Enter");
  await expect.poll(() => host.sentOfType("turn").at(-1)).toEqual(
    expect.objectContaining({ text: "Close the login bug", apps: ["linear"] }),
  );
  await expect(chips(page)).toHaveCount(0);
  await expect(page.getByTestId("apps-picked").locator('[data-picked-app="linear"]')).toBeVisible();
});

test("a chip can be removed before sending", async ({ page, host }) => {
  await openChat(page);
  await typeInComposer(page, "/git");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Remove GitHub" }).click();
  await expect(chips(page)).toHaveCount(0);
  await typeInComposer(page, "hi");
  await page.keyboard.press("Enter");
  await expect.poll(() => host.sentOfType("turn").length).toBe(1);
  expect(host.sentOfType("turn")[0]).not.toHaveProperty("apps");
});

test("the apps button opens the menu, and Manage goes to the Tools page", async ({ page }) => {
  await openChat(page);
  await page.getByTestId("composer-apps-button").click();
  await expect(menu(page).getByRole("option").first()).toBeVisible();
  await page.getByTestId("apps-manage").click();
  await expect(page).toHaveURL(/\/settings\/\?section=tools$/);
});

test("with no connected apps the menu says so", async ({ page, host }) => {
  host.connectors.items = [connectorFixture("notion", "Notion", { status: "needs_auth" })];
  await openChat(page);
  await typeInComposer(page, "/");
  await expect(menu(page)).toContainText("No apps are connected yet.");
});

test("an app that is no longer connected says so under the message", async ({ page, host }) => {
  host.answerNext("ok");
  await openChat(page);
  await typeInComposer(page, "/git");
  await page.keyboard.press("Enter");
  host.connectors.items = host.connectors.items.map((c) => (c.name === "github" ? { ...c, enabled: false } : c));
  await typeInComposer(page, "go");
  await page.keyboard.press("Enter");
  await expect(page.locator("[data-app-dropped=unknown]")).toHaveText("GitHub isn't connected right now");
});

test("Retry sends the same apps again", async ({ page, host }) => {
  host.answerNext("first");
  await openChat(page);
  await typeInComposer(page, "/git");
  await page.keyboard.press("Enter");
  await typeInComposer(page, "Open an issue");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("message-actions")).toBeVisible();
  host.answerNext("again");
  await page.getByTestId("retry-turn").click();
  await expect.poll(() => host.sentOfType("turn").length).toBe(2);
  expect(host.sentOfType("turn").at(-1)).toEqual(expect.objectContaining({ text: "Open an issue", apps: ["github"] }));
});

test("a / that names no app is plain text, and Enter sends it @cross", async ({ page, host }) => {
  host.answerNext("first");
  await openChat(page);
  await typeInComposer(page, "please read /etc/hosts");
  await expect(menu(page)).toBeHidden();
  await page.keyboard.press("Enter");
  await expect.poll(() => host.sentOfType("turn").at(-1)).toEqual(expect.objectContaining({ text: "please read /etc/hosts" }));
  await expect(page.getByTestId("message-actions")).toBeVisible();
  await typeInComposer(page, "/new");
  await expect(menu(page)).toBeHidden();
  await page.keyboard.press("Enter");
  await expect.poll(() => host.sentOfType("turn").length).toBe(2);
});

test("a message refused as busy keeps its apps with its text", async ({ page, host }) => {
  await openChat(page);
  await typeInComposer(page, "/git");
  await page.keyboard.press("Enter");
  host.activeTurn = "elsewhere";
  await typeInComposer(page, "do it");
  await page.keyboard.press("Enter");
  await expect(input(page)).toHaveValue("do it");
  await expect(chips(page).locator('[data-picked-app="github"]')).toBeVisible();
});
