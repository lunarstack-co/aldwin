import AxeBuilder from "@axe-core/playwright";

import { expect, openChat, send, session, setDensity, test, useProjectTheme } from "./fixtures/test";

test.beforeEach(async ({ page }, testInfo) => {
  await useProjectTheme(page, testInfo);
});

const TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

async function axe(page: import("@playwright/test").Page, name: string) {
  // Anything mid-fade reads as low contrast; wait for the page fade-in (which starts a frame late) and other finite motion.
  await page.waitForFunction(() => {
    const fade = document.querySelector("[data-testid=page-fade]");
    if (fade && getComputedStyle(fade).opacity !== "1") return false;
    return document.getAnimations().every((a) => a.effect?.getComputedTiming().iterations === Infinity || a.playState !== "running");
  });
  const result = await new AxeBuilder({ page }).withTags(TAGS).exclude("[data-sonner-toaster]").analyze();
  const summary = result.violations.map((v) => `${name}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target.join(" ")}`);
  expect(summary).toEqual([]);
}

test("no axe violations on the chat states", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha", "2026-11-11T09:00:00Z", true)];
  await openChat(page);
  await axe(page, "empty home");
  await send(page, "q");
  host.toolCall();
  await axe(page, "running");
  host.toolResult({ ok: false, summary: "no" });
  host.todo([{ text: "One", status: "in_progress" }, { text: "Two", status: "pending" }]);
  await host.stream("Some **text**");
  host.done({ reason: "max_steps", max_steps: 3 });
  await axe(page, "max steps with plan");
  await setDensity(page, "detail");
  await axe(page, "detail");
  await send(page, "again");
  host.error("x", "It failed");
  await axe(page, "error");
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await axe(page, "see all");
});

test("no axe violations on onboarding, take over and not found", async ({ page, host }) => {
  host.status = { ...host.status, keys_ok: false, host_ready: false, profile_done: false };
  await page.goto("/onboarding/connect/");
  await expect(page.getByRole("button", { name: "Connect", exact: true })).toBeVisible();
  await axe(page, "connect");
  host.status = { ...host.status, keys_ok: true, host_ready: true };
  await page.goto("/onboarding/personality/");
  await expect(page.getByRole("group")).toBeVisible();
  await axe(page, "personality");
  host.status = { ...host.status, profile_done: true };
  await page.goto("/nope/");
  await axe(page, "not found");
  host.refuseWith = { code: 4409, reason: "already_attached" };
  await page.goto("/");
  await expect(page.getByTestId("take-over")).toBeVisible();
  await axe(page, "take over");
});

test("the whole flow works without a pointer", async ({ page, host }) => {
  host.status = { ...host.status, profile_done: false };
  await page.goto("/onboarding/name/");
  const skip = page.getByRole("button", { name: "Skip for now" });
  for (let i = 0; i < 12 && !(await skip.evaluate((el) => el === document.activeElement)); i += 1) await page.keyboard.press("Tab");
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("composer-input")).toBeFocused({ timeout: 10_000 });
  await page.keyboard.type("hello");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("composer-stop")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("composer-send")).toBeVisible();
  await page.getByTestId("density-toggle").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toBeHidden();
  const ring = await page.getByTestId("density-toggle").evaluate((el) => {
    const style = getComputedStyle(el);
    return style.outlineStyle !== "none" || style.boxShadow !== "none";
  });
  expect(ring).toBe(true);
});

test("icon buttons are labelled", async ({ page, host }) => {
  host.answerNext("Hi.");
  await openChat(page);
  await send(page, "q");
  await expect(page.getByTestId("message-actions")).toBeVisible();
  const unnamed = await page.evaluate(() =>
    [...document.querySelectorAll("button")]
      .filter((b) => !b.textContent?.trim() && !b.getAttribute("aria-label") && !b.getAttribute("aria-labelledby") && !b.title)
      .map((b) => b.outerHTML.slice(0, 120)),
  );
  expect(unnamed).toEqual([]);
});
