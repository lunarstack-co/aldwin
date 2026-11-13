import { expect, ids, openChat, session, test } from "./fixtures/test";

test("opens Conversation JSON, formats the saved body, and copies exact JSON @cross", async ({
  page,
  host,
  context,
  browserName,
}) => {
  if (browserName === "chromium") {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  }
  const savedBody = {
    meta: {
      id: "s1",
      created_at: "2026-11-12T09:00:00Z",
      updated_at: "2026-11-12T09:05:00Z",
      model: "openai/gpt-4o",
      title: "Trip notes",
      interrupted: false,
    },
    memory: '{"version":1,"steps":[]}',
    turns: [{ text: "Plan the trip", skills: [] }],
  };
  host.sessions = [session("s1", "Trip notes")];
  host.current = host.sessions[0]!;
  host.conversations.set("s1", { body: savedBody });

  await openChat(page, "/c/?id=s1");
  await page.getByTestId("density-toggle").click();
  await page.getByTestId("conversation-json-trigger").click();

  const sheet = page.getByTestId("conversation-json-sheet");
  await expect(sheet).toBeVisible();
  expect(host.calledPaths()).toContain("GET /v1/conversations/s1");

  const expectedText = JSON.stringify(savedBody, null, 2);
  await expect(sheet.getByTestId("conversation-json-body")).toHaveText(expectedText);

  await sheet.getByTestId("conversation-json-copy").click();
  await expect(sheet.getByTestId("conversation-json-copy")).toContainText("Copied");
  if (browserName === "chromium") {
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(expectedText);
  }
});

test("shows loading on slow response, 404 message, and disables with no conversation or offline", async ({
  page,
  host,
}) => {
  await openChat(page);
  await page.getByTestId("density-toggle").click();
  await expect(page.getByTestId("conversation-json-trigger")).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Escape");

  host.sessions = [session("s_slow", "Slow"), session("s_404", "Missing")];
  host.current = host.sessions[0]!;
  host.conversations.set("s_slow", { delayMs: 600, body: { ok: true } });
  host.conversations.set("s_404", { status: 404 });

  await openChat(page, "/c/?id=s_slow");
  await page.getByTestId("density-toggle").click();
  await page.getByTestId("conversation-json-trigger").click();
  await expect(page.getByTestId("conversation-json-loading")).toBeVisible();
  await expect(page.getByTestId("conversation-json-body")).toBeVisible();
  await page.keyboard.press("Escape");

  // Offline disables the menu item.
  host.ticketFailures = { remaining: 20, status: 502 };
  await host.drop();
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "reconnecting");
  await page.getByTestId("density-toggle").click();
  await expect(page.getByTestId("conversation-json-trigger")).toHaveAttribute("aria-disabled", "true");
  await page.keyboard.press("Escape");

  // An unknown id shows the not-found message.
  host.ticketFailures = { remaining: 0, status: 0 };
  host.current = host.sessions[1]!;
  await openChat(page, "/c/?id=s_404");
  await page.getByTestId("density-toggle").click();
  await page.getByTestId("conversation-json-trigger").click();
  await expect(page.getByTestId("conversation-json-error")).toContainText("could not be found");
});
