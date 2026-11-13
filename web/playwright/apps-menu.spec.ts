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

// Enter only picks once the menu lists the app; a slow machine can otherwise send the keystroke first.
async function pickApp(page: Page, query: string, name: RegExp) {
  await typeInComposer(page, query);
  await expect(menu(page).getByRole("option", { name })).toBeVisible();
  await page.keyboard.press("Enter");
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
  await pickApp(page, "/lin", /Linear/);
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
  await pickApp(page, "/git", /GitHub/);
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
  await expect(page.getByTestId("apps-button-menu").getByRole("option").first()).toBeVisible();
  await page.getByTestId("apps-button-menu-manage").click();
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
  await pickApp(page, "/git", /GitHub/);
  host.connectors.items = host.connectors.items.map((c) => (c.name === "github" ? { ...c, enabled: false } : c));
  await typeInComposer(page, "go");
  await page.keyboard.press("Enter");
  await expect(page.locator("[data-app-dropped=unknown]")).toHaveText("GitHub isn't connected right now");
});

test("Retry sends the same apps again", async ({ page, host }) => {
  host.answerNext("first");
  await openChat(page);
  await pickApp(page, "/git", /GitHub/);
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
  await pickApp(page, "/git", /GitHub/);
  host.activeTurn = "elsewhere";
  await typeInComposer(page, "do it");
  await page.keyboard.press("Enter");
  await expect(input(page)).toHaveValue("do it");
  await expect(chips(page).locator('[data-picked-app="github"]')).toBeVisible();
});

test("a reloaded message still shows the apps it was sent with, and Retry sends them again", async ({ page, host }) => {
  host.history = [
    { role: "user", text: "Close the login bug", apps: [{ name: "linear", title: "Linear" }] },
    { role: "assistant", text: "Closed." },
  ];
  await openChat(page);
  const picked = page.getByTestId("apps-picked");
  await expect(picked.locator('[data-picked-app="linear"]')).toHaveText("Linear");
  await expect(picked.locator('svg[data-brand="linear"]')).toBeVisible();
  host.answerNext("again");
  await page.getByTestId("retry-turn").click();
  await expect.poll(() => host.sentOfType("turn").at(-1)).toEqual(
    expect.objectContaining({ text: "Close the login bug", apps: ["linear"] }),
  );
});

test("a reloaded message sent without apps shows no chips", async ({ page, host }) => {
  host.history = [
    { role: "user", text: "Hello" },
    { role: "assistant", text: "Hi." },
  ];
  await openChat(page);
  await expect(page.getByTestId("user-message")).toBeVisible();
  await expect(page.getByTestId("apps-picked")).toHaveCount(0);
});

test("a hostile app title from history shows as plain text", async ({ page, host }) => {
  let dialogs = 0;
  page.on("dialog", (dialog) => {
    dialogs += 1;
    void dialog.dismiss();
  });
  host.history = [
    { role: "user", text: "x", apps: [{ name: "evil", title: "<img src=x onerror=alert(1)>" }] },
    { role: "assistant", text: "ok" },
  ];
  await openChat(page);
  const chip = page.getByTestId("apps-picked").locator('[data-picked-app="evil"]');
  await expect(chip.locator(".truncate")).toHaveText("<img src=x onerror=alert(1)>");
  await expect(page.getByTestId("apps-picked").locator("img")).toHaveCount(0);
  expect(dialogs).toBe(0);
});
