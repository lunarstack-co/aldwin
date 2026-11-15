import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { LIMIT_RULES, settingsFixture } from "./fixtures/settings-fixtures";
import { expect, openChat, test, useProjectTheme } from "./fixtures/test";

const HEADINGS = ["What Aldwin reads", "Long chats", "Per-message limits"];
const SLIDER = "Summarise older messages at";
// In the order they appear on the page; the slider sits between notes days and steps.
const NUMBERS = [
  { label: "Profile length", field: "profile_chars" },
  { label: "Personality length", field: "persona_chars" },
  { label: "Recent notes length", field: "notes_chars" },
  { label: "Recent notes days", field: "notes_days" },
  { label: "Steps per answer", field: "max_steps" },
  { label: "Web searches per answer", field: "web_searches" },
  { label: "App tools offered at once", field: "connector_tools" },
] as const;
const ORDER = ["profile_chars", "persona_chars", "notes_chars", "notes_days", "context_compact_percent", "max_steps", "web_searches", "connector_tools"];
// 640px is also how a 1280px window lays out at 200% zoom.
const WIDTHS = [320, 375, 414, 640, 768, 1024, 1280, 1440, 1920];

const steps = (page: Page) => page.getByRole("spinbutton", { name: "Steps per answer" });
const slider = (page: Page) => page.getByRole("slider", { name: SLIDER });
const saveBar = (page: Page) => page.getByRole("region", { name: "Unsaved changes" });
const patches = (host: { calls: { method: string; body: unknown }[] }) => host.calls.filter((c) => c.method === "PATCH").map((c) => c.body);

async function openAdvanced(page: Page): Promise<void> {
  await openChat(page, "/settings/?section=advanced");
  await expect(page.getByRole("heading", { name: HEADINGS[0] })).toBeVisible();
}

async function sidewaysOverflow(page: Page): Promise<{ page: number; settings: number }> {
  return page.evaluate(() => {
    const doc = document.documentElement;
    const hub = document.querySelector<HTMLElement>("[data-testid=settings-hub]");
    return { page: doc.scrollWidth - doc.clientWidth, settings: hub ? hub.scrollWidth - hub.clientWidth : 0 };
  });
}

// Anything mid-fade reads as low contrast, so wait for the page fade-in and other finite motion first.
async function settle(page: Page): Promise<void> {
  await page.waitForFunction(() => {
    const fade = document.querySelector("[data-testid=page-fade]");
    if (fade && getComputedStyle(fade).opacity !== "1") return false;
    return document.getAnimations().every((a) => a.effect?.getComputedTiming().iterations === Infinity || a.playState !== "running");
  });
}

test.beforeEach(async ({ page, host }, testInfo) => {
  await useProjectTheme(page, testInfo);
  host.settings = settingsFixture();
});

test("fits every screen from 320px to 1920px without sideways scrolling @cross", async ({ page }, testInfo) => {
  const phone = testInfo.project.use.isMobile === true;
  for (const width of phone ? [page.viewportSize()?.width ?? 0] : WIDTHS) {
    if (!phone) await page.setViewportSize({ width, height: 900 });
    await openAdvanced(page);
    for (const name of HEADINGS) await expect(page.getByRole("heading", { name })).toBeVisible();
    expect(await sidewaysOverflow(page), `${width}px`).toEqual({ page: 0, settings: 0 });
    await steps(page).fill("60");
    await expect(saveBar(page)).toBeVisible();
    expect(await sidewaysOverflow(page), `${width}px with the save bar`).toEqual({ page: 0, settings: 0 });
    await saveBar(page).getByRole("button", { name: "Discard" }).click();
    await expect(saveBar(page)).toHaveCount(0);
  }
});

test("rows stack on a phone and sit side by side on a desktop @cross", async ({ page }, testInfo) => {
  const label = page.getByTestId("row-max_steps").getByText("Steps per answer", { exact: true });
  const below = async () => {
    const [text, control] = [await label.boundingBox(), await steps(page).boundingBox()];
    return control!.y >= text!.y + text!.height;
  };
  const beside = async () => {
    const [text, control] = [await label.boundingBox(), await steps(page).boundingBox()];
    return control!.x >= text!.x + text!.width;
  };
  if (testInfo.project.use.isMobile) {
    await openAdvanced(page);
    expect(await below()).toBe(true);
    return;
  }
  await page.setViewportSize({ width: 375, height: 800 });
  await openAdvanced(page);
  expect(await below()).toBe(true);
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect.poll(beside).toBe(true);
});

test("an edit shows the save bar, and Discard puts the value back @cross", async ({ page, host }) => {
  await openAdvanced(page);
  await expect(saveBar(page)).toHaveCount(0);
  const days = page.getByRole("spinbutton", { name: "Recent notes days" });
  await days.fill("10");
  await expect(saveBar(page)).toBeVisible();
  await expect(saveBar(page)).toContainText("Unsaved changes");
  await saveBar(page).getByRole("button", { name: "Discard" }).click();
  await expect(days).toHaveValue("7");
  await expect(saveBar(page)).toHaveCount(0);
  expect(patches(host)).toEqual([]);
});

test("a value outside the range shows an error by the field and blocks Save @cross", async ({ page, host }) => {
  await openAdvanced(page);
  const input = steps(page);
  for (const value of ["0", "-5", "2.5", "1001", ""]) {
    await input.fill(value);
    const error = page.locator("#limit-max_steps-error");
    await expect(error, `"${value}"`).toHaveText("Enter a whole number from 1 to 1,000.");
    await expect(input).toHaveAttribute("aria-invalid", "true");
    await expect(input).toHaveAttribute("aria-describedby", /\blimit-max_steps-error\b/);
    await expect(saveBar(page).getByRole("button", { name: "Save" })).toBeDisabled();
  }
  await input.fill("1000");
  await expect(page.locator("#limit-max_steps-error")).toHaveCount(0);
  await expect(input).not.toHaveAttribute("aria-invalid", "true");
  await expect(saveBar(page).getByRole("button", { name: "Save" })).toBeEnabled();
  expect(patches(host)).toEqual([]);
});

test("the summarise slider stays between 60% and 90% by keyboard and pointer @cross", async ({ page }, testInfo) => {
  await openAdvanced(page);
  const thumb = slider(page);
  const value = page.getByTestId("limit-context_compact_percent-value");
  // A native range input, so assistive tech reads the range from min and max.
  await expect(thumb).toHaveAttribute("min", "60");
  await expect(thumb).toHaveAttribute("max", "90");
  await expect(thumb).toHaveAttribute("aria-valuetext", "60%");
  await expect(value).toHaveText("60%");

  if (!testInfo.project.use.isMobile) {
    await thumb.focus();
    await page.keyboard.press("ArrowLeft");
    await expect(thumb).toHaveAttribute("aria-valuenow", "60");
    await page.keyboard.press("ArrowRight");
    await expect(thumb).toHaveAttribute("aria-valuenow", "65");
    await expect(value).toHaveText("65%");
    await expect(saveBar(page)).toBeVisible();
    await page.keyboard.press("End");
    await page.keyboard.press("ArrowRight");
    await expect(thumb).toHaveAttribute("aria-valuenow", "90");
    await page.keyboard.press("Home");
    await page.keyboard.press("ArrowLeft");
    await expect(thumb).toHaveAttribute("aria-valuenow", "60");
  }

  const control = page.getByTestId("row-context_compact_percent").locator("[data-slot=slider-control]");
  // Centred, so the save bar that appears after the first change never sits over it.
  await control.evaluate((el) => el.scrollIntoView({ block: "center" }));
  const box = (await control.boundingBox())!;
  const middle = box.y + box.height / 2;
  if (testInfo.project.use.isMobile) {
    await page.touchscreen.tap(box.x + box.width - 1, middle);
    await expect(thumb).toHaveAttribute("aria-valuenow", "90");
    await page.touchscreen.tap(box.x + 1, middle);
    await expect(thumb).toHaveAttribute("aria-valuenow", "60");
    return;
  }
  await page.mouse.move(box.x + box.width / 2, middle);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width + 300, middle, { steps: 5 });
  await page.mouse.up();
  await expect(thumb).toHaveAttribute("aria-valuenow", "90");
  await page.mouse.move(box.x + box.width / 2, middle);
  await page.mouse.down();
  await page.mouse.move(Math.max(0, box.x - 300), middle, { steps: 5 });
  await page.mouse.up();
  await expect(thumb).toHaveAttribute("aria-valuenow", "60");
});

test("a saved value is still there after a reload @cross", async ({ page, host }) => {
  await openAdvanced(page);
  await steps(page).fill("120");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByRole("status")).toHaveText("Saved");
  await expect(saveBar(page)).toHaveCount(0);
  expect(patches(host)).toEqual([{ limits: { max_steps: 120 } }]);
  await page.reload();
  await expect(steps(page)).toHaveValue("120");
});

test("Reset to defaults puts back the default each description shows, and still needs Save @cross", async ({ page, host }) => {
  host.settings.limits = {
    ...host.settings.limits,
    profile_chars: 9000,
    persona_chars: 3000,
    notes_chars: 5000,
    notes_days: 14,
    context_compact_percent: 80,
    max_steps: 200,
    web_searches: 9,
    connector_tools: 60,
  };
  await openAdvanced(page);
  await page.getByRole("button", { name: "Reset to defaults" }).click();
  const dialog = page.getByRole("dialog", { name: "Reset advanced settings?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Reset" }).click();
  await expect(dialog).toHaveCount(0);

  for (const { label, field } of NUMBERS) {
    const description = await page.locator(`#limit-${field}-hint`).innerText();
    const shown = description.match(/Default: ([\d,]+)/)?.[1]?.replace(/,/g, "");
    expect(Number(shown), field).toBe(LIMIT_RULES[field].default);
    await expect(page.getByRole("spinbutton", { name: label })).toHaveValue(String(shown));
  }
  await expect(page.locator("#limit-context_compact_percent-hint")).toContainText("Default: 60%.");
  await expect(slider(page)).toHaveAttribute("aria-valuenow", "60");
  await expect(saveBar(page)).toBeVisible();
  expect(patches(host)).toEqual([]);
});

test("Tab reaches every setting in the order shown, and Enter saves @cross", async ({ page, host, browserName }, testInfo) => {
  test.skip(testInfo.project.use.isMobile === true, "phones have no Tab key");
  await openAdvanced(page);
  // Safari on macOS moves between text fields with Tab, and to everything with Option+Tab.
  const tab = browserName === "webkit" ? "Alt+Tab" : "Tab";
  const reached: string[] = [];
  await page.getByRole("spinbutton", { name: "Profile length" }).focus();
  for (let i = 0; i < ORDER.length; i += 1) {
    if (i > 0) await page.keyboard.press(tab);
    reached.push(await page.evaluate(() => document.activeElement?.closest<HTMLElement>("[data-testid^=row-]")?.dataset.testid?.slice(4) ?? ""));
  }
  expect(reached).toEqual(ORDER);
  // Firefox on macOS only moves between form fields unless the system says otherwise.
  if (browserName !== "firefox") {
    await page.keyboard.press(tab);
    await expect(page.getByRole("button", { name: "Reset to defaults" })).toBeFocused();
  }

  await steps(page).focus();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type("75");
  await page.keyboard.press("Enter");
  await expect(saveBar(page).getByRole("status")).toHaveText("Saved");
  expect(patches(host)).toEqual([{ limits: { max_steps: 75 } }]);
});

test("every control on a phone is at least 44px tall @cross", async ({ page }, testInfo) => {
  test.skip(testInfo.project.use.isMobile !== true, "touch sizes apply to phones");
  await openAdvanced(page);
  await steps(page).fill("60");
  await expect(saveBar(page)).toBeVisible();
  await settle(page);
  const small = await page.getByTestId("settings-hub").evaluate((root) =>
    [...root.querySelectorAll<HTMLElement>("a[href], button, input, [data-slot=slider-control]")]
      .filter((el) => !(el instanceof HTMLInputElement && el.type === "range"))
      .map((el) => ({ el, box: el.getBoundingClientRect() }))
      // Phones snap sizes to whole device pixels, so a 44px control can measure a fraction under.
      .filter(({ box }) => box.width > 0 && box.height > 0 && box.height < 43.5)
      .map(({ el, box }) => `${el.tagName.toLowerCase()} "${el.getAttribute("aria-label") ?? el.textContent?.trim() ?? el.id}" ${Math.round(box.height)}px`),
  );
  expect(small).toEqual([]);
});

test("the save bar never covers the field being edited @cross", async ({ page }) => {
  await openAdvanced(page);
  const input = steps(page);
  // Park the field on the bottom edge of the screen, right where the bar will appear.
  const start = (await input.boundingBox())!;
  const height = page.viewportSize()!.height;
  await page.getByTestId("settings-hub").evaluate((el, delta) => el.scrollBy(0, delta), start.y + start.height - (height - 4));
  // An out-of-range value is the hard case: the bar grows a line and an error appears under the field.
  await input.fill("1001");
  await expect(saveBar(page)).toContainText("Fix the highlighted settings to save.");
  await settle(page);
  const bar = (await saveBar(page).boundingBox())!;
  for (const part of [input, page.locator("#limit-max_steps-error")]) {
    const box = (await part.boundingBox())!;
    expect(box.y + box.height).toBeLessThanOrEqual(bar.y + 0.5);
  }
});

test("at the bottom of the page the save bar covers nothing @cross", async ({ page }) => {
  await openAdvanced(page);
  await steps(page).fill("60");
  await expect(saveBar(page)).toBeVisible();
  await page.getByTestId("settings-hub").evaluate((el) => el.scrollTo(0, el.scrollHeight));
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  const bar = (await saveBar(page).boundingBox())!;
  for (const target of [page.getByTestId("row-connector_tools"), page.getByRole("button", { name: "Reset to defaults" })]) {
    const box = (await target.boundingBox())!;
    expect(box.y + box.height).toBeLessThanOrEqual(bar.y + 0.5);
  }
});

test("a failed save keeps the bar open and says what went wrong @cross", async ({ page, host }) => {
  await openAdvanced(page);
  await page.route("**/v1/settings", (route) =>
    route.request().method() === "PATCH"
      ? route.fulfill({ status: 500, json: { detail: "Aldwin couldn't write config.yaml." } })
      : route.fallback(),
  );
  await steps(page).fill("60");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByRole("alert")).toHaveText("Aldwin couldn't write config.yaml.");
  await expect(saveBar(page).getByRole("button", { name: "Save" })).toBeEnabled();
  await expect(steps(page)).toHaveValue("60");
  expect(host.settings.limits.max_steps).toBe(50);
});

test("a value above the page's range set in config.yaml is shown with a note @cross", async ({ page, host }) => {
  host.settings.limits = { ...host.settings.limits, max_steps: 150_000_000 };
  await openAdvanced(page);
  await expect(page.locator("#limit-max_steps-note")).toHaveText(
    "Your config.yaml sets 150,000,000 steps. You can set up to 1,000 steps here.",
  );
  await expect(steps(page)).not.toHaveAttribute("aria-invalid", "true");
  await page.getByRole("spinbutton", { name: "Recent notes days" }).fill("10");
  await saveBar(page).getByRole("button", { name: "Save" }).click();
  await expect(saveBar(page).getByRole("status")).toHaveText("Saved");
  expect(patches(host)).toEqual([{ limits: { notes_days: 10 } }]);
});

test("Check for updates lives on About, not Advanced @cross", async ({ page, host }) => {
  await openAdvanced(page);
  await expect(page.getByTestId("settings-advanced")).not.toContainText("Check for updates");
  await page.getByTestId("settings-nav").getByRole("link", { name: "About" }).click();
  await expect(page.getByTestId("about-version")).toHaveText("0.0.6");
  const toggle = page.getByRole("switch", { name: /Check for updates/ });
  await expect(toggle).toBeChecked();
  await toggle.click();
  await expect(toggle).not.toBeChecked();
  await expect.poll(() => patches(host)).toEqual([{ check_for_updates: false }]);
  expect(host.settings.check_for_updates).toBe(false);
});

test("passes axe while editing, with an error, and with the reset dialog open", async ({ page }) => {
  await openAdvanced(page);
  await steps(page).fill("0");
  await expect(saveBar(page)).toBeVisible();
  await settle(page);
  const scan = async () => (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).exclude("[data-sonner-toaster]").analyze()).violations;
  expect((await scan()).map((v) => `${v.id}: ${v.nodes[0]?.target.join(" ")}`)).toEqual([]);
  await page.getByRole("button", { name: "Reset to defaults" }).click();
  await expect(page.getByRole("dialog", { name: "Reset advanced settings?" })).toBeVisible();
  await settle(page);
  expect((await scan()).map((v) => `${v.id}: ${v.nodes[0]?.target.join(" ")}`)).toEqual([]);
});

test("looks the same at 375, 768 and 1440px", async ({ page }) => {
  test.skip(Boolean(process.env.CI) || process.platform !== "darwin", "the saved screenshots are made on macOS");
  for (const width of [375, 768, 1440]) {
    await page.setViewportSize({ width, height: 1800 });
    await openAdvanced(page);
    await settle(page);
    await expect(page).toHaveScreenshot(`advanced-${width}.png`, { animations: "disabled" });
  }
});
