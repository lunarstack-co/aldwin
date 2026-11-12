import { existsSync } from "node:fs";
import { join } from "node:path";

import { expect, openChat, send, session, test } from "./fixtures/test";

test("todo updates drive the todo list and map all four statuses", async ({ page, host }) => {
  expect(existsSync(join(__dirname, "..", "src", "components", "assistant-ui", "agent-plan.tsx"))).toBe(false);
  await openChat(page);
  await send(page, "plan it");
  host.todo([
    { text: "Find", status: "done" },
    { text: "Read", status: "in_progress" },
    { text: "Write", status: "pending" },
    { text: "Skip", status: "dropped" },
  ]);
  const plan = page.getByTestId("todo-list");
  await expect(plan).toContainText("1/3");
  await expect(plan).toContainText("Done");
  await expect(plan).toContainText("In progress");
  await expect(plan).toContainText("To do");
  await expect(plan).toContainText("Dropped");
  host.todo([
    { text: "Find", status: "done" },
    { text: "Read", status: "done" },
    { text: "Write", status: "pending" },
  ]);
  await expect(plan).toContainText("2/3");
});

test("no todos no plan", async ({ page, host }) => {
  await openChat(page);
  host.todo([]);
  await expect(page.getByTestId("todo-list")).toHaveCount(0);
});

test("the page asks for todos after history", async ({ page, host }) => {
  await openChat(page);
  await expect.poll(() => host.sent.map((f) => f.type).slice(0, 5)).toEqual([
    "hello",
    "status",
    "history",
    "sessions.list",
    "todo.get",
  ]);
});

test("todos land between turns and from todo.get", async ({ page, host }) => {
  host.todos = [{ text: "Saved task", status: "pending" }];
  await openChat(page);
  await expect(page.getByTestId("todo-list")).toContainText("0/1");
  host.todo([{ text: "Saved task", status: "done" }]);
  await expect(page.getByTestId("todo-list")).toHaveCount(0);
});

test("the plan folds, and leaves once the work is finished or stopped", async ({ page, host }) => {
  await openChat(page);
  await send(page, "plan it");
  host.todo([
    { text: "First", status: "done" },
    { text: "Second", status: "in_progress" },
  ]);
  const plan = page.getByTestId("todo-list");
  const toggle = page.getByTestId("todo-list-toggle");
  const wide = (page.viewportSize()?.width ?? 1280) >= 768;
  await expect(toggle).toHaveAttribute("aria-expanded", wide ? "true" : "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", wide ? "false" : "true");
  await expect(plan).toContainText("1/2");
  host.done({ reason: "answered" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await send(page, "carry on");
  host.todo([
    { text: "First", status: "done" },
    { text: "Second", status: "done" },
  ]);
  await expect(plan).toContainText("2/2");
  host.done({ reason: "answered" });
  await expect(plan).toHaveCount(0);
  await send(page, "an unrelated question");
  await host.stream("Sure.");
  await expect(plan).toHaveCount(0);
  host.done({ reason: "answered" });
  await send(page, "new plan");
  host.todo([{ text: "Another", status: "pending" }]);
  await expect(plan).toContainText("0/1");
  host.done({ reason: "stopped" });
  await expect(plan).toHaveCount(0);
  await send(page, "tell me a joke");
  await host.stream("Why did the lighthouse");
  await expect(plan).toHaveCount(0);
  host.done({ reason: "answered" });
  await expect(plan).toHaveCount(0);
});

test("a plan waiting on the user carries into the reply, and stays away once it is older", async ({ page, host }) => {
  await openChat(page);
  await send(page, "plan it and ask me first");
  host.todo([
    { text: "First", status: "done" },
    { text: "Second", status: "pending" },
  ]);
  host.done({ reason: "answered" });
  const plan = page.getByTestId("todo-list");
  await expect(plan).toContainText("1/2");
  await send(page, "yes");
  await host.stream("Doing step two.");
  await expect(plan).toContainText("1/2");
  host.done({ reason: "answered" });
  await expect(plan).toHaveCount(0);
});

test("new chat clears the plan", async ({ page, host }) => {
  host.answerNext("Done.");
  await openChat(page);
  await send(page, "q");
  host.todo([{ text: "Task", status: "pending" }], "host");
  await expect(page.getByTestId("todo-list")).toBeVisible();
  await page.getByTestId("new-chat").filter({ visible: true }).click();
  await expect(page.getByTestId("empty-home")).toBeVisible();
  await expect(page.getByTestId("todo-list")).toHaveCount(0);
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
