import { expect, ids, openChat, send, session, test, TURN_BUSY } from "./fixtures/test";

test("a stopped turn keeps its text", async ({ page, host }) => {
  await openChat(page);
  await send(page, "q");
  await host.stream("Partial");
  host.done({ reason: "stopped" });
  const turn = page.getByTestId("turn").last();
  await expect(turn).toContainText("Partial");
  await expect(turn).toContainText("Stopped before the answer was finished.");
  await expect(turn.getByTestId("retry-turn")).toBeVisible();
});

test("a turn stopped before any text offers retry", async ({ page, host }) => {
  await openChat(page);
  await send(page, "q");
  host.done({ reason: "stopped" });
  const turn = page.getByTestId("turn").last();
  await expect(turn).toContainText("Stopped before the answer was finished.");
  await turn.getByTestId("retry-turn").click();
  await expect.poll(() => host.sentOfType("turn").map((f) => f.text)).toEqual(["q", "q"]);
});

test("the step limit is named", async ({ page, host }) => {
  await openChat(page);
  await send(page, "q");
  await host.stream("Some");
  host.done({ reason: "max_steps", max_steps: 20 });
  await expect(page.getByTestId("turn").last()).toContainText("Stopped after reaching the step limit of 20 steps.");
  await send(page, "again");
  await host.stream("More");
  host.done({ reason: "max_steps" });
  await expect(page.getByTestId("turn").last()).toContainText("Stopped after reaching the step limit for one turn.");
});

test("an error offers retry and copy", async ({ page, host, context, browserName }) => {
  if (browserName === "chromium") await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await openChat(page);
  await send(page, "q");
  host.error("provider_error", "Rate limited by OpenAI");
  const row = page.getByTestId("error-row");
  await expect(row).toContainText("Rate limited by OpenAI");
  await expect(row.getByTestId("error-code")).toHaveText("provider_error");
  await row.getByTestId("copy-error").click();
  await expect(row.getByTestId("copy-error")).toContainText("Copied");
  if (browserName === "chromium") {
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("Rate limited by OpenAI");
  }
  host.answerNext("Second try.");
  await row.getByTestId("retry-error").click();
  await expect.poll(() => host.sentOfType("turn").map((f) => f.text)).toEqual(["q", "q"]);
});

test("server goodbye ends at aldwin stopped", async ({ page, host }) => {
  await openChat(page);
  const sockets = host.sockets.length;
  await host.drop(1001);
  const stopped = page.getByTestId("aldwin-stopped");
  await expect(stopped).toContainText("Aldwin stopped");
  await expect(stopped.getByRole("button", { name: "Reload" })).toBeVisible();
  await page.waitForTimeout(1500);
  expect(host.sockets.length).toBe(sockets);
});

test("a turn the page did not know about is followed when the host says busy", async ({ page, host }) => {
  host.sessions = [session("a", "Research", "2026-11-11T09:00:00Z", true)];
  host.current = host.sessions[0];
  host.history = [{ role: "user", text: "Research agent architectures" }];
  host.interrupted = true;
  host.activeTurn = "t-old";
  await openChat(page, "/c/?id=a");
  await send(page, "continue");
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: TURN_BUSY })).toBeVisible();
  await expect(page.getByTestId("error-row")).toHaveCount(0);
  await expect(page.getByTestId(ids.input)).toHaveValue("continue");
  await expect(page.getByTestId("composer-stop")).toBeVisible();
  await expect(page.getByTestId("user-message")).toHaveCount(1);
  await expect(page.getByTestId("interrupted-badge")).toHaveCount(0);
  await expect(page.getByTestId("history-interrupted")).toHaveCount(0);
  await host.stream("Here is the summary.");
  host.done();
  const turn = page.getByTestId("turn").last();
  await expect(turn).toContainText("Here is the summary.");
  await expect(page.getByTestId("composer-stop")).toHaveCount(0);
});

test("opening the page while a turn streams shows it running", async ({ page, host }) => {
  host.sessions = [session("a", "Research", "2026-11-11T09:00:00Z", true)];
  host.current = host.sessions[0];
  host.history = [{ role: "user", text: "Research agent architectures" }];
  host.interrupted = true;
  host.activeTurn = "t-old";
  host.statusReply = { message: "", turn_streamed_chunks: 2, active_tool: null, partial_text: "So far" };
  await openChat(page, "/c/?id=a");
  await expect(page.getByTestId("turn").last()).toContainText("So far");
  await expect(page.getByTestId("composer-stop")).toBeVisible();
  await expect(page.getByTestId("user-message")).toHaveCount(1);
  expect(host.sentOfType("status")).toHaveLength(1);
});
