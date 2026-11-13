import type { Page } from "@playwright/test";

import { expect, openChat, send, test } from "./fixtures/test";

const picker = (page: Page) => page.getByTestId("model-picker-composer");
const menu = (page: Page) => page.getByTestId("model-picker-menu");

async function openPicker(page: Page): Promise<void> {
  await picker(page).click();
  await expect(menu(page)).toBeVisible();
}

test("the picker groups models by provider @cross", async ({ page }) => {
  await openChat(page);
  await openPicker(page);
  await expect(menu(page).locator('[data-provider="openai"]')).toContainText("OpenAI");
  await expect(menu(page).locator('[data-provider="anthropic"]')).toContainText("Anthropic");
  await expect(menu(page).locator('svg[data-brand="openai"]').first()).toBeVisible();
  const llama = menu(page).locator('[data-model="ollama/llama3.1"]');
  await expect(llama).toHaveAttribute("aria-disabled", "true");
  await expect(llama).toContainText("Not running");
  await expect(menu(page).locator('[data-provider="mistral"]')).toHaveCount(0);
  await menu(page).getByRole("combobox", { name: "Search models" }).fill("mini");
  await expect(menu(page).getByRole("option")).toHaveText(["GPT-4o mini"]);
});

test("picking a model changes it everywhere @cross", async ({ page, host }) => {
  await openChat(page);
  await openPicker(page);
  await menu(page).getByRole("option", { name: "Claude Sonnet 4" }).click();
  await expect(picker(page)).toContainText("Claude Sonnet 4");
  await expect(picker(page).locator('svg[data-brand="anthropic"]')).toBeVisible();
  expect(host.sentOfType("set_model")).toEqual([expect.objectContaining({ name: "anthropic/claude-sonnet-4" })]);
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await page.getByTestId("settings").filter({ visible: true }).click();
  await expect(page.getByTestId("model-picker-settings")).toContainText("Claude Sonnet 4");
});

test("the picker is disabled while an answer is on its way", async ({ page }) => {
  await openChat(page);
  await send(page, "q");
  await expect(picker(page)).toBeDisabled();
});

test("a busy host keeps the model and says why", async ({ page, host }) => {
  await openChat(page);
  await openPicker(page);
  host.activeTurn = "elsewhere";
  await menu(page).getByRole("option", { name: "Claude Sonnet 4" }).click();
  await expect(page.getByText("Aldwin is still working on your last request.")).toBeVisible();
  await expect(picker(page)).toContainText("GPT-4o");
});

test("a new chat keeps the chosen model", async ({ page, host }) => {
  host.answerNext("ok");
  await openChat(page);
  await send(page, "q");
  await expect(page.getByTestId("message-actions")).toBeVisible();
  await openPicker(page);
  await menu(page).getByRole("option", { name: "Claude Sonnet 4" }).click();
  await expect(picker(page)).toContainText("Claude Sonnet 4");
  await page.getByTestId("new-chat").filter({ visible: true }).click();
  await expect(picker(page)).toContainText("Claude Sonnet 4");
  expect(host.sentOfType("set_model")).toHaveLength(1);
});

test("with no provider set up the picker links to Settings", async ({ page, host }) => {
  host.models = { current: "", providers: [] };
  await openChat(page);
  await openPicker(page);
  await menu(page).getByTestId("model-picker-add-provider").click();
  await expect(page).toHaveURL(/\/settings\/\?section=providers$/);
});

test("the button shows the model's name, not its id, without a hover @cross", async ({ page, host }) => {
  await openChat(page);
  await expect(picker(page)).toContainText("GPT-4o");
  await expect(picker(page)).not.toContainText("gpt-4o");
  // Asked for after the conversation, so it never slows opening a chat.
  expect(host.sent.map((f) => f.type).indexOf("models.list")).toBeGreaterThan(host.sent.map((f) => f.type).indexOf("history"));
});

test("the list is asked for once", async ({ page, host }) => {
  await openChat(page);
  await expect.poll(() => host.sentOfType("models.list").length).toBe(1);
  await openPicker(page);
  await page.keyboard.press("Escape");
  await expect(menu(page)).toBeHidden();
  await openPicker(page);
  expect(host.sentOfType("models.list")).toHaveLength(1);
});

test("the button keeps its name and look as the menu first opens", async ({ page }) => {
  await openChat(page);
  const before = await picker(page).evaluate((el) => ({ text: el.textContent, border: getComputedStyle(el).borderTopWidth }));
  await picker(page).hover();
  await expect(picker(page)).toContainText("GPT-4o");
  await picker(page).click();
  await expect(menu(page)).toBeVisible();
  const after = await picker(page).evaluate((el) => ({ text: el.textContent, border: getComputedStyle(el).borderTopWidth }));
  expect(after.border).toBe(before.border);
  expect(after.text).toBe("GPT-4o");
});

test("if the list can't load, the button still names the model", async ({ page, host }) => {
  host.failing.add("models.list");
  await openChat(page);
  await expect.poll(() => host.sentOfType("models.list").length).toBe(1);
  await expect(picker(page)).toContainText("gpt-4o");
  await expect(picker(page).locator("span.opacity-0")).toHaveCount(0);
});

test("only the model in use is filled; other rows stay plain @cross", async ({ page }) => {
  await openChat(page);
  await openPicker(page);
  await page.mouse.move(0, 0);
  const bg = (ref: string) => menu(page).locator(`[data-model="${ref}"]`).evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(await bg("openai/gpt-4o-mini")).toBe("rgba(0, 0, 0, 0)");
  expect(await bg("anthropic/claude-sonnet-4")).toBe("rgba(0, 0, 0, 0)");
  expect(await bg("openai/gpt-4o")).not.toBe("rgba(0, 0, 0, 0)");
});
