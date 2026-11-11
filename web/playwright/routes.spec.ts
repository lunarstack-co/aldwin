import { expect, openChat, send, session, test } from "./fixtures/test";

test("first turn gives the chat an address without a new history entry @cross", async ({ page, host }) => {
  host.answerNext("Done.");
  await openChat(page);
  const length = await page.evaluate(() => history.length);
  await send(page, "hello");
  await expect(page).toHaveURL(/\/c\/\?id=s1$/);
  expect(await page.evaluate(() => history.length)).toBe(length);
});

test("back and forward move between conversations @cross", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha"), session("b", "Beta")];
  await openChat(page);
  const rail = async () => {
    if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  };
  await rail();
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Alpha" }).click();
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Alpha.");
  await rail();
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Beta" }).click();
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Beta.");
  await page.goBack();
  await expect(page).toHaveURL(/\?id=a$/);
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Alpha.");
  await page.goForward();
  await expect(page).toHaveURL(/\?id=b$/);
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Beta.");
  expect(host.sentOfType("sessions.open").map((f) => f.session_id)).toEqual(["a", "b", "a", "b"]);
});

test("reload reopens the conversation @cross", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  await openChat(page, "/c/?id=a");
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Alpha.");
  expect(host.sentOfType("sessions.open").map((f) => f.session_id)).toEqual(["a"]);
});

test("a drop while opening asks again after reconnect", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  host.holdOpen = true;
  await openChat(page, "/c/?id=a");
  await expect.poll(() => host.sentOfType("sessions.open")).toHaveLength(1);
  host.holdOpen = false;
  await host.drop();
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Alpha.");
  await expect(page).toHaveURL(/\/c\/\?id=a$/);
  expect(host.sentOfType("sessions.open").map((f) => f.session_id)).toEqual(["a", "a"]);
});

test("no open for the current conversation", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  host.current = host.sessions[0];
  host.history = [{ role: "user", text: "q" }, { role: "assistant", text: "Current answer." }];
  await openChat(page, "/c/?id=a");
  await expect(page.getByTestId("assistant-message")).toContainText("Current answer.");
  expect(host.sentOfType("sessions.open")).toHaveLength(0);
});

for (const bad of ["../../etc", "", "a b", "<script>", "a".repeat(129)]) {
  test(`a bad id never reaches the host: ${JSON.stringify(bad).slice(0, 20)}`, async ({ page, host }) => {
    await page.goto(`/c/?id=${encodeURIComponent(bad)}`);
    await expect(page).toHaveURL(/127\.0\.0\.1:4173\/$/);
    await expect(page.locator("[data-sonner-toast]")).toContainText("That conversation could not be opened.");
    expect(host.sentOfType("sessions.open")).toHaveLength(0);
    if (bad) {
      expect(await page.content()).not.toContain(bad.length > 100 ? bad : `>${bad}<`);
      expect(await page.title()).not.toContain(bad);
    }
  });
}

test("a 128 character id is allowed", async ({ page, host }) => {
  const id = "a".repeat(128);
  host.sessions = [session(id, "Long")];
  await openChat(page, `/c/?id=${id}`);
  await expect.poll(() => host.sentOfType("sessions.open").map((f) => f.session_id)).toEqual([id]);
});

test("a missing conversation lands home and refreshes", async ({ page }) => {
  await page.goto("/c/?id=gone");
  await expect(page.getByTestId("session-notice")).toContainText("no longer exists");
  await expect(page).toHaveURL(/127\.0\.0\.1:4173\/$/);
});

test("back while busy keeps the address", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha"), session("b", "Beta")];
  await openChat(page);
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Alpha" }).click();
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Alpha.");
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Beta" }).click();
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Beta.");
  await send(page, "keep going");
  await expect(page.getByTestId("composer-stop")).toBeVisible();
  await page.goBack();
  await expect(page.getByTestId("session-notice")).toContainText("Still working");
  await expect(page).toHaveURL(/\?id=b$/);
  await host.stream("streaming on");
  await expect(page.getByTestId("assistant-message").last()).toContainText("streaming on");
});
