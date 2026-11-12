import { expect, ids, openChat, send, session, test } from "./fixtures/test";

test("nothing shows in the conversation until its history arrives", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  host.current = host.sessions[0];
  host.history = [{ role: "user", text: "q" }, { role: "assistant", text: "Kept answer." }];
  host.holdHello = true;
  await page.goto("/c/?id=a");
  const thread = page.getByTestId("thread");
  await expect(thread).toHaveAttribute("data-settled", "false");
  await expect(thread).toHaveCSS("opacity", "0");
  await expect(page.getByTestId("history-empty")).toHaveCount(0);
  host.release();
  await expect(thread).toHaveAttribute("data-settled", "true");
  await expect(page.getByTestId("assistant-message")).toContainText("Kept answer.");
  await expect(thread).toHaveCSS("opacity", "1");
});

test("a fast connection never shows connecting", async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as { __banner: boolean };
    w.__banner = false;
    new MutationObserver(() => {
      const status = document.querySelector("[data-testid=connection-status]");
      if (status && !status.classList.contains("sr-only") && status.textContent?.includes("Connecting")) w.__banner = true;
    }).observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  });
  await openChat(page);
  await page.reload();
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "open");
  expect(await page.evaluate(() => (window as unknown as { __banner: boolean }).__banner)).toBe(false);
});

test("connecting shows the dot matrix until the socket opens", async ({ page, host }) => {
  let release: () => void = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  host.rest.set("POST /v1/ws-ticket", async (route) => {
    await held;
    await route.fulfill({ json: { ticket: "t-1", expires_in: 30 } });
  });
  await page.goto("/");
  const status = page.getByTestId(ids.status);
  await expect(status).toHaveAttribute("data-phase", "connecting");
  await expect(status).toContainText("Connecting");
  await expect(status.locator("[data-slot=dot-matrix], [data-state=loading]").first()).toBeAttached();
  await expect(page.getByTestId(ids.input)).toBeDisabled();
  await expect(page.getByTestId(ids.send)).toBeDisabled();
  release();
  await expect(status).toHaveAttribute("data-phase", "open");
});

test("a drop mid stream catches up @cross", async ({ page, host }) => {
  await openChat(page);
  await send(page, "hello");
  await host.stream("Hel");
  host.statusReply = { message: "Reading the page", turn_streamed_chunks: 2, active_tool: null, partial_text: "Hello" };
  host.history = [{ role: "user", text: "hello" }];
  await host.drop();
  const status = page.getByTestId(ids.status);
  await expect(status).toHaveAttribute("data-phase", "open", { timeout: 10_000 });
  await expect(page.getByTestId("turn").last()).toContainText("Hello");
  await expect(page.getByTestId("thinking")).toHaveCount(0);
  await expect(page.getByTestId("user-message")).toHaveCount(1);
});

test("catch up shows the status message", async ({ page, host }) => {
  await openChat(page);
  await send(page, "hello");
  host.statusReply = { message: "Reading the page", turn_streamed_chunks: 1, active_tool: "fetch", partial_text: null };
  host.history = [{ role: "user", text: "hello" }];
  await host.drop();
  // A tool is running, so the status line rides on the work chip rather than its own row.
  await expect(page.getByTestId("turn").last()).toContainText("Reading the page", { timeout: 10_000 });
});

test("gives up after five tries in all", async ({ page, host }) => {
  await openChat(page);
  host.ticketFailures = { remaining: 100, status: 502 };
  await host.drop();
  await expect(page.getByTestId("aldwin-stopped")).toBeVisible({ timeout: 30_000 });
  // The first ticket opened the page; the drop counts as try one, then four more.
  expect(host.calls.filter((c) => c.path === "/v1/ws-ticket").length).toBe(5);
});

test("retry reconnects now", async ({ page, host }) => {
  await openChat(page);
  host.ticketFailures = { remaining: 2, status: 502 };
  await host.drop();
  const retry = page.getByTestId("connection-retry");
  await expect(retry).toBeVisible({ timeout: 10_000 });
  const before = host.calls.filter((c) => c.path === "/v1/ws-ticket").length;
  await retry.click();
  await expect.poll(() => host.calls.filter((c) => c.path === "/v1/ws-ticket").length).toBeGreaterThan(before);
});

test("waits while no host is serving", async ({ page, host }) => {
  host.ticketFailures = { remaining: 1, status: 503 };
  await page.goto("/");
  const status = page.getByTestId(ids.status);
  await expect(status).toHaveAttribute("data-phase", "not_ready");
  await expect(status).toContainText("Waiting for Aldwin to start");
  await status.getByTestId("connection-retry").click();
  await expect(status).toHaveAttribute("data-phase", "open");
});

test("take over another tab", async ({ page, host }) => {
  host.refuseWith = { code: 4409, reason: "already_attached" };
  await page.goto("/");
  const dialog = page.getByTestId("take-over");
  await expect(dialog).toContainText("Open in another tab");
  await expect(page.getByTestId("take-over-button")).toBeFocused();
  await expect(dialog).not.toHaveCSS("box-shadow", "none");
  await expect(page.getByTestId("composer")).toHaveCSS("box-shadow", "none");
  host.refuseWith = null;
  await page.getByTestId("take-over-button").click();
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "open");
  expect(host.socketUrls.at(-1)).toContain("take_over=1");
});

test("interrupted history shows a callout", async ({ page, host }) => {
  host.history = [
    { role: "user", text: "half" },
    { role: "assistant", text: "Half an answ" },
  ];
  host.interrupted = true;
  await openChat(page);
  const notice = page.getByTestId("history-interrupted");
  await expect(notice).toContainText("cut off");
  await expect(notice).toHaveCSS("border-inline-start-color", /.+/);
  host.answerNext("Fresh.");
  await page.getByTestId("composer-input").fill("again");
  await page.getByTestId("composer-input").press("Enter");
  await expect(notice).toHaveCount(0);
});

test("nothing opens while disconnected", async ({ page, host }) => {
  host.sessions = [{ id: "a", created_at: "", updated_at: "2026-11-11T09:00:00Z", model: "m", title: "Alpha", interrupted: false }];
  await openChat(page);
  // The list arrives after the socket opens; drop only once it is on screen.
  await expect(page.getByTestId("history-row").first()).toBeVisible();
  host.ticketFailures = { remaining: 100, status: 502 };
  await host.drop();
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "reconnecting");
  await expect(page.getByTestId("see-all").filter({ visible: true })).toBeDisabled();
  await expect(page.getByTestId("history-row").first()).toBeDisabled();
});

test("never answers an approval, says the answer is waiting, and offers Stop @cross", async ({ page, host }) => {
  await openChat(page);
  await send(page, "write it");
  host.approval({ summary: "Write file" });
  const notice = page.getByTestId("approval-notice");
  await expect(notice).toContainText("It wants to: Write file.");
  await expect(notice).toContainText("isn't available in the web app yet");
  await page.waitForTimeout(500);
  expect(host.sentOfType("approve")).toHaveLength(0);
  await notice.getByRole("button", { name: "Stop" }).click();
  await expect.poll(() => host.sentOfType("stop").length).toBe(1);
  await expect(notice).toHaveCount(0);
});
