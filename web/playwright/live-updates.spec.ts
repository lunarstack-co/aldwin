import type { Page } from "@playwright/test";

import { connectorFixture } from "./fixtures/settings-fixtures";
import { expect, openChat, test, type FakeHost } from "./fixtures/test";

// The real host answers changes with `done` alone, as the fake now does: the page must re-read its lists.

function connectBrave(host: FakeHost) {
  host.search.items = host.search.items.map((i) => (i.name === "brave" ? { ...i, connected: true, source: "file", detail: "key-…9999" } : i));
}

async function toChat(page: Page) {
  await page.getByRole("link", { name: "Back to chat" }).click();
  await expect(page.getByTestId("composer-input")).toBeVisible();
}

test("choosing an engine in Settings shows in the composer at once @cross", async ({ page, host }) => {
  connectBrave(host);
  await openChat(page, "/settings/?section=search");
  await page.locator('[data-engine="brave"]').getByRole("button", { name: "Use this one" }).click();
  await expect(page.locator('[data-engine="brave"]')).toContainText("In use");
  await expect(page.locator('[data-engine="tavily"]')).not.toContainText("In use");
  await toChat(page);
  await expect(page.getByTestId("search-picker")).toHaveAccessibleName("Web search: Brave");
  expect(host.sentOfType("search.list").length).toBeGreaterThanOrEqual(2);
});

test("choosing an engine in the composer shows in Settings at once", async ({ page, host }) => {
  connectBrave(host);
  await openChat(page);
  await page.getByTestId("search-picker").click();
  await page.getByTestId("search-picker-menu").getByRole("menuitemradio", { name: "Brave" }).click();
  await expect(page.getByTestId("search-picker")).toHaveAccessibleName("Web search: Brave");
  await page.getByTestId("settings").filter({ visible: true }).click();
  await page.getByTestId("settings-nav").getByRole("link", { name: "Search" }).click();
  await expect(page.locator('[data-engine="brave"]')).toContainText("In use");
});

test("connecting and disconnecting an engine update the list without a reload", async ({ page }) => {
  await openChat(page, "/settings/?section=search");
  const brave = page.locator('[data-engine="brave"]');
  await brave.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Brave API key").fill("brave-key-1234");
  await brave.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(brave).toContainText("Connected");
  await brave.getByRole("button", { name: "Disconnect" }).click();
  await expect(brave).toContainText("Not connected");
});

test("disconnecting a provider updates Settings and the model picker", async ({ page }) => {
  await openChat(page, "/settings/?section=providers");
  await page.locator('[data-provider="anthropic"]').getByRole("button", { name: "Disconnect" }).click();
  await expect(page.locator('[data-provider="anthropic"]')).toContainText("Not set up");
  await toChat(page);
  await page.getByTestId("model-picker-composer").click();
  await expect(page.getByTestId("model-picker-menu").locator('[data-provider="anthropic"]')).toHaveCount(0);
});

test("adding, turning off and removing an app show in the / menu at once", async ({ page, host }) => {
  host.connectors.items.push(connectorFixture("linear", "Linear", { tools: host.connectors.items[0].tools.slice(0, 2) }));
  await openChat(page, "/settings/?section=tools");
  await page.getByTestId("connector-enabled-linear").click();
  await expect(page.locator('[data-connector="linear"]')).toContainText("Off");
  await toChat(page);
  await page.getByTestId("composer-input").click();
  await page.keyboard.type("/");
  await expect(page.getByTestId("apps-menu").getByRole("option")).toHaveText([/GitHub/]);
});

test("each composer menu ends with a short Manage link", async ({ page }) => {
  await openChat(page);
  await page.getByTestId("model-picker-composer").click();
  await expect(page.getByTestId("model-picker-manage")).toHaveText("Manage models");
  await page.getByTestId("model-picker-manage").click();
  await expect(page).toHaveURL(/\/settings\/\?section=providers$/);
  await page.getByRole("link", { name: "Back to chat" }).click();
  await page.getByTestId("search-picker").click();
  await expect(page.getByTestId("search-picker-manage")).toHaveText("Manage search");
  await page.keyboard.press("Escape");
  await page.getByTestId("composer-input").click();
  await page.keyboard.type("/");
  await expect(page.getByTestId("apps-manage")).toHaveText("Manage apps");
});
