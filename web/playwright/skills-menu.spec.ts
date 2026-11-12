import type { Page } from "@playwright/test";

import { expect, ids, newSkill, openChat, send, SKILL_FIXTURE, skillsOf, test } from "./fixtures/test";

const menu = (page: Page) => page.getByRole("listbox", { name: "Skills" });
const options = (page: Page) => menu(page).getByRole("option");
const input = (page: Page) => page.getByTestId(ids.input);

async function typeInComposer(page: Page, text: string): Promise<void> {
  await input(page).click();
  await page.keyboard.type(text);
}

function lastTurnFrame(host: { sentOfType: (type: string) => Record<string, unknown>[] }) {
  return host.sentOfType("turn").at(-1);
}

test("typing @ opens the skills menu, recent first, with no tools or uploads @cross", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(menu(page)).toBeVisible();
  await expect(options(page)).toHaveText([/^web-research/, /^files/, /^memory/, /^ship-notes/]);
  await expect(menu(page)).not.toContainText(/tool/i);
  await expect(menu(page)).not.toContainText(/upload|attach/i);
});

test("the @ button opens the menu in the composer @cross", async ({ page }) => {
  await openChat(page);
  await page.getByTestId("composer-skills").click();
  await expect(input(page)).toBeFocused();
  await expect(input(page)).toHaveValue("@");
  await expect(menu(page)).toBeVisible();
});

test("the @ button adds a space after text", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "hi");
  await page.getByTestId("composer-skills").click();
  await expect(input(page)).toHaveValue("hi @");
  await expect(menu(page)).toBeVisible();
});

test("@ inside a word or address does not open the menu", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "mail fola@cuvama");
  await expect(menu(page)).toHaveCount(0);
});

test("the catalogue is asked for once, on first open", async ({ page, host }) => {
  await openChat(page);
  expect(host.sentOfType("skills.list")).toHaveLength(0);
  await typeInComposer(page, "@");
  await expect(options(page)).toHaveCount(4);
  await page.keyboard.press("Escape");
  await page.keyboard.type(" and @");
  await expect(options(page)).toHaveCount(4);
  expect(host.sentOfType("skills.list")).toHaveLength(1);
});

test("typing after @ filters the list", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "@we");
  await expect(options(page)).toHaveText([/^web-research/]);
});

test("no match says so and Enter sends nothing", async ({ page, host }) => {
  await openChat(page);
  await typeInComposer(page, "@zzz");
  await expect(menu(page)).toContainText("No matching skills");
  await page.keyboard.press("Enter");
  expect(host.sentOfType("turn")).toHaveLength(0);
});

test("arrow keys and Enter insert a skill @cross", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(options(page)).toHaveCount(4);
  await page.keyboard.press("ArrowDown");
  await expect(options(page).nth(1)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(input(page)).toHaveValue("@files ");
  await expect(menu(page)).toHaveCount(0);
  await expect(input(page)).toBeFocused();
});

test("clicking a skill inserts it", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "hi @");
  await options(page).filter({ hasText: "memory" }).click();
  await expect(input(page)).toHaveValue("hi @memory ");
});

test("Esc closes the menu and keeps the text", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "hi @me");
  await expect(menu(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu(page)).toHaveCount(0);
  await expect(input(page)).toHaveValue("hi @me");
  await expect(input(page)).toBeFocused();
});

test("clicking elsewhere closes the menu", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(menu(page)).toBeVisible();
  await page.getByTestId("empty-home").click();
  await expect(menu(page)).toHaveCount(0);
  await expect(input(page)).toHaveValue("@");
});

test("Send puts the picked skill on the turn @cross", async ({ page, host }) => {
  await openChat(page);
  await typeInComposer(page, "@web");
  await page.keyboard.press("Enter");
  await page.keyboard.type("summarise");
  await page.keyboard.press("Enter");
  await expect.poll(() => lastTurnFrame(host)?.skills).toEqual(["web-research"]);
  expect(String(lastTurnFrame(host)?.text)).toContain("@web-research");
});

test("two skills, a typed token and duplicates", async ({ page, host }) => {
  await openChat(page);
  await send(page, "@files and @memory please");
  await expect.poll(() => lastTurnFrame(host)?.skills).toEqual(["files", "memory"]);
  host.done();
  await send(page, "use @files please, @files too");
  await expect.poll(() => lastTurnFrame(host)?.skills).toEqual(["files"]);
});

test("used skills show as chips and dropped ones say why", async ({ page, host }) => {
  await openChat(page);
  await send(page, "@files @memory @ship-notes @web-research @ghost go");
  await expect.poll(() => lastTurnFrame(host)?.skills).toEqual(["files", "memory", "ship-notes", "web-research", "ghost"]);
  const message = page.getByTestId("user-message").last();
  await expect(message.locator("[data-slot=directive-text-chip]")).toHaveCount(5);
  await expect(message.locator("[data-slot=directive-text-chip]").first()).toHaveText("files");
  const used = message.getByTestId("skills-used");
  await expect(used.locator("[data-skill-used]")).toHaveText(["files", "memory", "ship-notes"]);
  await expect(used).toContainText("@web-research wasn't used: up to 3 skills per message");
  await expect(used).toContainText("@ghost wasn't found");
});

test("no turn ever carries tools", async ({ page, host }) => {
  await openChat(page);
  await send(page, "@files go");
  await expect.poll(() => host.sentOfType("turn").length).toBe(1);
  host.done();
  await send(page, "plain question");
  await expect.poll(() => host.sentOfType("turn").length).toBe(2);
  for (const frame of host.sentOfType("turn")) expect(frame).not.toHaveProperty("tools");
});

test("a broken skill is never offered", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "@br");
  await expect(menu(page)).toContainText("No matching skills");
  await expect(options(page)).toHaveCount(0);
});

test("loading shows while the catalogue is on its way", async ({ page, host }) => {
  host.holdSkills = true;
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(menu(page)).toContainText("Loading skills…");
  await page.keyboard.type("me");
  await expect(input(page)).toHaveValue("@me");
  host.releaseSkills();
  await expect(options(page)).toHaveText([/^memory/]);
});

test("an empty catalogue says so and the composer still sends", async ({ page, host }) => {
  host.skills = [];
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(menu(page)).toContainText("No skills yet");
  await page.keyboard.press("Escape");
  await page.keyboard.type(" hello");
  await page.keyboard.press("Enter");
  await expect.poll(() => host.sentOfType("turn").length).toBe(1);
});

test("a failed catalogue offers Try again", async ({ page, host }) => {
  host.skillsListError = "boom";
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(menu(page)).toContainText("Couldn't load skills.");
  host.skillsListError = null;
  await page.getByTestId("skills-retry").click();
  await expect(options(page)).toHaveCount(4);
  expect(host.sentOfType("skills.list")).toHaveLength(2);
  await expect(input(page)).toBeFocused();
});

test("a new skill from the host appears without asking again", async ({ page, host }) => {
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(options(page)).toHaveCount(4);
  await page.keyboard.press("Escape");
  host.emitSkills([...SKILL_FIXTURE, newSkill("daily-brief")]);
  await page.keyboard.type(" @da");
  await expect(options(page)).toHaveText([/^daily-brief/]);
  expect(host.sentOfType("skills.list")).toHaveLength(1);
});

test("at most eight rows show", async ({ page, host }) => {
  host.skills = skillsOf(12);
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(options(page)).toHaveCount(8);
  await page.keyboard.type("s1");
  await expect(options(page)).toHaveText([/^s10/, /^s11/, /^s12/]);
});

test("skill text from files stays plain text", async ({ page, host }) => {
  const dialogs: string[] = [];
  page.on("dialog", (dialog) => {
    dialogs.push(dialog.message());
    void dialog.dismiss();
  });
  host.skills = [newSkill("odd", { description: "<img src=x onerror=alert(1)>**bold**" })];
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(menu(page)).toContainText("<img src=x onerror=alert(1)>**bold**");
  await expect(menu(page).locator("img, strong")).toHaveCount(0);
  expect(dialogs).toEqual([]);
});

test("Manage in Settings sits disabled until Settings exists", async ({ page }) => {
  await openChat(page);
  await typeInComposer(page, "@");
  const manage = page.getByTestId("skills-manage");
  await expect(manage).toHaveText("Manage in Settings…");
  await expect(manage).toBeDisabled();
});

test("the menu works while a turn is running", async ({ page, host }) => {
  await openChat(page);
  await send(page, "first");
  await expect(page.getByTestId(ids.stop)).toBeVisible();
  await typeInComposer(page, "@fi");
  await page.keyboard.press("Enter");
  await expect(input(page)).toHaveValue("@files ");
  await expect(page.getByTestId(ids.stop)).toBeVisible();
  expect(host.sentOfType("turn")).toHaveLength(1);
});

test("Retry sends the same skills again", async ({ page, host }) => {
  await openChat(page);
  await send(page, "@files go");
  await expect.poll(() => host.sentOfType("turn").length).toBe(1);
  host.error("provider_error", "Rate limited");
  await page.getByTestId("retry-error").click();
  await expect.poll(() => host.sentOfType("turn").map((f) => f.skills)).toEqual([["files"], ["files"]]);
});

test("a drop while the catalogue loads never leaves it stuck", async ({ page, host }) => {
  host.holdSkills = true;
  await openChat(page);
  await typeInComposer(page, "@");
  await expect(menu(page)).toContainText("Loading skills…");
  host.holdSkills = false;
  await host.drop();
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "open", { timeout: 10_000 });
  await expect(options(page)).toHaveCount(4);
});

test("on phones the menu fits the screen with tall rows @cross", async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 1280) >= 768, "phones only");
  await openChat(page);
  await typeInComposer(page, "@");
  const box = (await menu(page).boundingBox())!;
  const width = page.viewportSize()!.width;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(width);
  for (const option of await options(page).all()) expect((await option.boundingBox())!.height).toBeGreaterThanOrEqual(40);
});
