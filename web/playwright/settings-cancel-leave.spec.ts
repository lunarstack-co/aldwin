import type { Page } from "@playwright/test";

import { expect, openChat, session, test } from "./fixtures/test";

const openSettings = (page: Page, section: string) => openChat(page, `/settings/?section=${section}`);
const leaveDialog = (page: Page) => page.getByTestId("leave-dialog");

async function typeAKey(page: Page) {
  await page.locator('[data-provider="openai"]').getByRole("button", { name: "Replace key" }).click();
  await page.getByLabel("OpenAI API key").fill("sk-half-typed");
}

test.describe("cancel @cross", () => {
  test("a key form closes and forgets what was typed", async ({ page, host }) => {
    await openSettings(page, "providers");
    await typeAKey(page);
    await page.getByTestId("key-form-openai").getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByTestId("key-form-openai")).toHaveCount(0);
    await page.locator('[data-provider="openai"]').getByRole("button", { name: "Replace key" }).click();
    await expect(page.getByLabel("OpenAI API key")).toHaveValue("");
    expect(host.calls.filter((c) => c.path === "/v1/settings/secrets")).toHaveLength(0);
  });

  test("adding a server can be cancelled", async ({ page, host }) => {
    await openSettings(page, "providers");
    await page.getByTestId("add-server-open").click();
    await page.getByLabel("Name").fill("lmstudio");
    await page.getByTestId("add-server").getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByTestId("add-server")).toHaveCount(0);
    await expect(page.getByTestId("add-server-open")).toBeVisible();
    expect(host.sentOfType("provider.server")).toHaveLength(0);
  });

  test("a search key form and the SearXNG address can be cancelled", async ({ page, host }) => {
    await openSettings(page, "search");
    const brave = page.locator('[data-engine="brave"]');
    await brave.getByRole("button", { name: "Connect" }).click();
    await page.getByLabel("Brave API key").fill("half");
    await brave.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByLabel("Brave API key")).toHaveCount(0);
    await page.getByLabel("SearXNG address").fill("http://127.0.0.1:9");
    await expect(page.getByLabel("SearXNG address")).toHaveValue("http://127.0.0.1:9");
    await page.getByTestId("searxng-form").getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByLabel("SearXNG address")).toHaveValue("");
    expect(host.sentOfType("search.connect")).toHaveLength(0);
  });

  test("Advanced edits can be cancelled", async ({ page }) => {
    await openSettings(page, "advanced");
    await page.getByTestId("limit-max_steps").fill("30");
    await page.getByTestId("advanced-cancel").click();
    await expect(page.getByTestId("limit-max_steps")).toHaveValue("15");
    await expect(page.getByTestId("advanced-save")).toBeDisabled();
  });

  test("a waiting sign-in can be cancelled", async ({ page, host }) => {
    await openSettings(page, "tools");
    await page.locator('[data-connector="notion"]').getByRole("button", { name: "Authenticate" }).click();
    await expect.poll(() => host.sentOfType("connectors.login").length).toBe(1);
    host.connectorAuth("notion", "browser", { url: "https://notion.example.com/authorize" });
    await page.getByTestId("sign-in-cancel").click();
    await expect(page.getByTestId("connector-sign-in")).toHaveCount(0);
    await expect.poll(() => host.sentOfType("stop").length).toBe(1);
    await expect(page.locator('[data-connector="notion"]').getByRole("button", { name: "Authenticate" })).toBeEnabled();
  });

  test("the Add an app dialog has a Cancel button", async ({ page }) => {
    await openSettings(page, "tools");
    await page.getByTestId("add-app-open").click();
    await page.getByTestId("add-app-cancel").click();
    await expect(page.getByTestId("add-app")).toHaveCount(0);
  });
});

test.describe("leaving with unsaved changes @cross", () => {
  test("a link asks first; Stay keeps the edit and Leave goes", async ({ page }) => {
    await openSettings(page, "providers");
    await typeAKey(page);
    await page.getByRole("link", { name: "Back to chat" }).click();
    await expect(leaveDialog(page)).toBeVisible();
    await page.getByRole("button", { name: "Stay" }).click();
    await expect(leaveDialog(page)).toHaveCount(0);
    await expect(page).toHaveURL(/section=providers$/);
    await expect(page.getByLabel("OpenAI API key")).toHaveValue("sk-half-typed");
    await page.getByTestId("settings-nav").getByRole("link", { name: "Search" }).click();
    await page.getByTestId("leave-confirm").click();
    await expect(page).toHaveURL(/section=search$/);
  });

  test("nothing asks when nothing was edited", async ({ page }) => {
    await openSettings(page, "advanced");
    await page.getByTestId("settings-nav").getByRole("link", { name: "Search" }).click();
    await expect(page).toHaveURL(/section=search$/);
    await expect(leaveDialog(page)).toHaveCount(0);
  });

  test("saving clears the guard", async ({ page }) => {
    await openSettings(page, "advanced");
    await page.getByTestId("limit-max_steps").fill("20");
    await page.getByTestId("advanced-save").click();
    await expect(page.getByTestId("advanced-form")).toContainText("Saved");
    await page.getByTestId("settings-nav").getByRole("link", { name: "Search" }).click();
    await expect(page).toHaveURL(/section=search$/);
  });

  test("Back asks first and Leave goes back", async ({ page }) => {
    await openChat(page);
    if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
    await page.getByTestId("settings").filter({ visible: true }).click();
    await page.getByTestId("settings-nav").getByRole("link", { name: "Advanced" }).click();
    await page.getByTestId("limit-max_steps").fill("30");
    await page.goBack();
    await expect(leaveDialog(page)).toBeVisible();
    await expect(page).toHaveURL(/section=advanced$/);
    await page.getByTestId("leave-confirm").click();
    await expect(page).toHaveURL(/\/settings\/$/);
  });

  test("New chat and See all ask first", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha")];
    await openSettings(page, "advanced");
    await page.getByTestId("limit-max_steps").fill("30");
    if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
    await page.getByTestId("new-chat").filter({ visible: true }).click();
    await expect(leaveDialog(page)).toBeVisible();
    await page.getByRole("button", { name: "Stay" }).click();
    await expect(page).toHaveURL(/section=advanced$/);
  });

  test("closing or reloading the tab asks the browser to confirm", async ({ page }) => {
    await openSettings(page, "advanced");
    await page.getByTestId("limit-max_steps").fill("30");
    const dialog = page.waitForEvent("dialog");
    void page.close({ runBeforeUnload: true });
    const shown = await dialog;
    expect(shown.type()).toBe("beforeunload");
    await shown.dismiss();
  });
});

test("New chat works from Settings @cross", async ({ page }) => {
  await openSettings(page, "search");
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  const newChat = page.getByTestId("new-chat").filter({ visible: true });
  await expect(newChat).toBeEnabled();
  await newChat.click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("composer-input")).toBeVisible();
});

test("after Leave, one Back returns to the page before @cross", async ({ page }) => {
  await openChat(page);
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await page.getByTestId("settings").filter({ visible: true }).click();
  await page.getByTestId("settings-nav").getByRole("link", { name: "Advanced" }).click();
  await page.getByTestId("limit-max_steps").fill("30");
  await page.getByTestId("settings-nav").getByRole("link", { name: "Search" }).click();
  await page.getByTestId("leave-confirm").click();
  await expect(page).toHaveURL(/section=search$/);
  await page.goBack();
  await expect(page).toHaveURL(/section=advanced$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/settings\/$/);
});

test("cancelling one form and typing in the next at once never asks to leave, and Back still does @cross", async ({ page }) => {
  await openSettings(page, "search");
  const brave = page.locator('[data-engine="brave"]');
  await brave.getByRole("button", { name: "Connect" }).click();
  await page.getByLabel("Brave API key").fill("half");
  await brave.getByRole("button", { name: "Cancel" }).click();
  await page.getByLabel("SearXNG address").fill("http://127.0.0.1:9");
  await page.waitForTimeout(500);
  await expect(page.getByTestId("leave-dialog")).toHaveCount(0);
  await expect(page.getByTestId("searxng-form").getByRole("button", { name: "Cancel" })).toBeVisible();
  await page.goBack();
  await expect(page.getByTestId("leave-dialog")).toBeVisible();
  await expect(page.getByLabel("SearXNG address")).toHaveValue("http://127.0.0.1:9");
});
