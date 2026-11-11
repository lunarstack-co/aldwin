import { expect, openChat, send, session, test } from "./fixtures/test";

test("todo updates drive the plan", async ({ page, host }) => {
  await openChat(page);
  await send(page, "plan it");
  host.todo([
    { text: "Find", status: "done" },
    { text: "Read", status: "in_progress" },
    { text: "Write", status: "pending" },
  ]);
  const plan = page.getByTestId("agent-plan");
  await expect(plan.getByTestId("agent-plan-progress")).toHaveText("1/3");
  await expect(plan.locator("li").nth(1)).toHaveAttribute("data-status", "in_progress");
  host.todo([
    { text: "Find", status: "done" },
    { text: "Read", status: "done" },
    { text: "Write", status: "pending" },
  ]);
  await expect(plan.getByTestId("agent-plan-progress")).toHaveText("2/3");
});

test("no todos no plan", async ({ page, host }) => {
  await openChat(page);
  host.todo([]);
  await expect(page.getByTestId("agent-plan")).toHaveCount(0);
});

test("the page asks for todos after history", async ({ page, host }) => {
  await openChat(page);
  await expect.poll(() => host.sent.map((f) => f.type).slice(0, 5)).toEqual(["hello", "status", "history", "sessions.list", "todo.get"]);
});

test("todos land between turns and from todo.get", async ({ page, host }) => {
  host.todos = [{ text: "Saved task", status: "pending" }];
  await openChat(page);
  await expect(page.getByTestId("agent-plan-progress")).toHaveText("0/1");
  host.todo([{ text: "Saved task", status: "done" }]);
  await expect(page.getByTestId("agent-plan-progress")).toHaveText("1/1");
});

test("new chat clears the plan", async ({ page, host }) => {
  host.answerNext("Done.");
  await openChat(page);
  await send(page, "q");
  host.todo([{ text: "Task", status: "pending" }], "host");
  await expect(page.getByTestId("agent-plan")).toBeVisible();
  await page.getByTestId("new-chat").filter({ visible: true }).click();
  await expect(page.getByTestId("empty-home")).toBeVisible();
  await expect(page.getByTestId("agent-plan")).toHaveCount(0);
});

test("a notice is a toast that leaves", async ({ page, host }) => {
  await page.clock.install({ time: new Date("2026-11-11T10:00:00") });
  await openChat(page);
  host.notice("GitHub disconnected");
  const toast = page.locator("[data-sonner-toast]").filter({ hasText: "GitHub disconnected" });
  await expect(toast).toBeVisible();
  await expect(page.getByTestId("transcript")).not.toContainText("GitHub disconnected");
  await page.clock.runFor(7000);
  await expect(toast).toHaveCount(0);
});

test("notices show on see all", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  await openChat(page);
  await page.getByTestId("see-all").filter({ visible: true }).click();
  host.notice("Search provider changed");
  await expect(page.getByTestId("history-see-all")).toBeVisible();
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: "Search provider changed" })).toBeVisible();
});
