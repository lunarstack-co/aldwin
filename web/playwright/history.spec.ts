import { expect, openChat, send, session, test } from "./fixtures/test";
import { sessions25 } from "./fixtures/seeds";

test("see all is hidden until there is a conversation", async ({ page, host }) => {
  host.answerNext("Hi.");
  await openChat(page);
  await expect(page.getByTestId("history-empty")).toBeVisible();
  await expect(page.getByTestId("see-all")).toHaveCount(0);
  await send(page, "hello");
  await expect(page.getByTestId("see-all").filter({ visible: true })).toBeVisible();
});

test("rail lists conversations with turns and marks none while the open one is empty", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha"), session("b", "Beta")];
  await openChat(page);
  await expect(page.getByTestId("history-row")).toHaveCount(2);
  await expect(page.locator('[data-testid=history-row][aria-current="page"]')).toHaveCount(0);
});

test("clicking a row opens it and gives it an address @cross", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha"), session("g", "Groceries")];
  await openChat(page);
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Groceries" }).click();
  await expect(page).toHaveURL(/\/c\/\?id=g$/);
  await expect.poll(() => host.sentOfType("sessions.open").map((f) => f.session_id)).toEqual(["g"]);
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await expect(page.locator('[data-testid=history-row][aria-current="page"]').filter({ visible: true })).toHaveText(/Groceries/);
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Groceries.");
});

test("rows open from the keyboard", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha"), session("b", "Beta")];
  await openChat(page);
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Beta" }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/c\/\?id=b$/);
});

test("rail keeps fifteen recent plus the open one", async ({ page, host }) => {
  host.sessions = sessions25();
  host.current = host.sessions[19];
  host.history = [{ role: "user", text: "q" }, { role: "assistant", text: "a" }];
  await openChat(page, "/c/?id=s20");
  await expect(page.getByTestId("history-row")).toHaveCount(16);
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await expect(page.getByTestId("see-all-row")).toHaveCount(25);
});

test("interrupted rows are marked", async ({ page, host }) => {
  host.sessions = sessions25();
  await openChat(page);
  await expect(page.getByTestId("interrupted-badge")).toHaveCount(1);
  await expect(page.getByTestId("history-row").nth(6).getByTestId("interrupted-badge")).toHaveText("Interrupted");
});

test("the rail does not show the user's name", async ({ page }) => {
  await openChat(page);
  await expect(page.getByTestId("rail")).not.toContainText("Ada");
});

test("settings is a link and @ is live", async ({ page }) => {
  await openChat(page);
  await expect(page.getByTestId("settings").filter({ visible: true })).toHaveAttribute("href", "/settings/");
  await expect(page.getByTestId("composer-skills")).toBeEnabled();
  await expect(page.getByTestId("model-picker-composer")).toHaveAccessibleName("Model: GPT-4o");
});

test("no rename archive or delete", async ({ page, host }) => {
  host.sessions = sessions25();
  await openChat(page);
  await expect(page.getByRole("button", { name: /rename|archive|delete|more/i })).toHaveCount(0);
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await expect(page.getByRole("button", { name: /rename|archive|delete|more/i })).toHaveCount(0);
});

test("search opens see all focused", async ({ page }) => {
  await openChat(page);
  await page.getByTestId("rail-search").filter({ visible: true }).click();
  await expect(page.getByTestId("history-see-all")).toBeVisible();
  await expect(page.getByTestId("history-search")).toBeFocused();
});

test("see all groups by day @cross", async ({ page, host }) => {
  await page.clock.setFixedTime(new Date("2026-11-11T10:00:00"));
  host.sessions = [
    session("t", "Today one", "2026-11-11T08:00:00"),
    session("y", "Yesterday one", "2026-11-10T08:00:00"),
    session("w", "Week one", "2026-11-06T08:00:00"),
    session("o", "Old one", "2026-10-20T08:00:00"),
  ];
  await openChat(page);
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await page.getByTestId("see-all").filter({ visible: true }).click();
  const headings = page.getByTestId("history-see-all").getByRole("heading", { level: 2 });
  await expect(headings).toHaveText(["Today", "Yesterday", "Previous 7 days", "Older"]);
  await expect(page.getByTestId("history-group-older")).toContainText("Old one");
});

test("search filters by title after a short pause", async ({ page, host }) => {
  host.sessions = sessions25();
  await openChat(page);
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await page.getByTestId("history-search").fill("PLAN");
  await expect(page.getByTestId("see-all-row")).toHaveCount(2);
  await expect(page.getByTestId("see-all-row")).toHaveText([/Plan the Lisbon trip/, /Planning taxes/]);
  await page.getByTestId("history-search").fill("");
  await expect(page.getByTestId("see-all-row")).toHaveCount(25);
  await page.getByTestId("history-search").fill("zzz");
  await expect(page.getByTestId("history-empty")).toHaveText('No conversations match "zzz".');
});

test("slash focuses search but not while typing", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  await openChat(page);
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await page.getByTestId("history-search").blur();
  await page.locator("body").press("/");
  await expect(page.getByTestId("history-search")).toBeFocused();
  await expect(page.getByTestId("history-search")).toHaveValue("");
});

test("esc and back leave see all", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  await openChat(page);
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("composer")).toBeVisible();
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await page.getByTestId("see-all-back").click();
  await expect(page.getByTestId("composer")).toBeVisible();
});

test("new chat on an empty conversation asks the host for nothing", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  await openChat(page);
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await page.getByTestId("new-chat").filter({ visible: true }).click();
  await expect(page.getByTestId("empty-home")).toBeVisible();
  expect(host.sentOfType("sessions.new")).toHaveLength(0);
  await expect(page).toHaveURL(/\/$/);
});

test("new chat after a turn asks the host once and goes home", async ({ page, host }) => {
  host.answerNext("Done.");
  await openChat(page);
  await send(page, "first");
  await expect(page).toHaveURL(/\/c\/\?id=/);
  await page.getByTestId("new-chat").filter({ visible: true }).click();
  await expect(page.getByTestId("empty-home")).toBeVisible();
  await expect(page).toHaveURL(/127\.0\.0\.1:4173\/$/);
  expect(host.sentOfType("sessions.new")).toHaveLength(1);
});

test("switching mid answer says busy and keeps the address", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  await openChat(page);
  await send(page, "long one");
  await expect(page.getByTestId("composer-stop")).toBeVisible();
  await host.stream("first words");
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Alpha" }).click();
  await expect(page.getByTestId("session-notice")).toContainText("Still working on the last answer");
  await host.stream("still going");
  await expect(page.getByTestId("assistant-message")).toContainText("still going");
  // The refusal refreshes the list, which gives the running chat its own address; never Alpha's.
  await expect(page).toHaveURL(/\/c\/\?id=s2$/);
  expect(host.sentOfType("sessions.open").map((f) => f.session_id)).toEqual(["a"]);
});

test("deleted elsewhere stays and refreshes", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha"), session("b", "Beta")];
  await openChat(page);
  host.openError = { code: "not_found", message: "That conversation no longer exists." };
  const lists = host.sentOfType("sessions.list").length;
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Beta" }).click();
  await expect(page.getByTestId("session-notice")).toContainText("no longer exists");
  await expect.poll(() => host.sentOfType("sessions.list").length).toBeGreaterThan(lists);
  await expect(page).toHaveURL(/127\.0\.0\.1:4173\/$/);
});

test("other failures show the host's words", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  await openChat(page);
  host.openError = { code: "io_error", message: "The conversation file could not be read." };
  await page.getByTestId("history-row").click();
  await expect(page.getByTestId("session-notice")).toContainText("The conversation file could not be read.");
});

test("a new conversation joins the rail under its title", async ({ page, host }) => {
  host.answerNext("Done.");
  await openChat(page);
  await send(page, "Weekend plan");
  await expect(page.getByTestId("history-row").first()).toHaveText(/Weekend plan/);
});
