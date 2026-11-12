import { expect, openChat, send, session, setDensity, test } from "./fixtures/test";

test("a stopped answer keeps its text, Continue sends a turn, and Discard hides until reload @cross", async ({
  page,
  host,
}) => {
  host.sessions = [session("s1", "Partial chat")];
  host.current = host.sessions[0]!;
  host.histories.set("s1", []);
  await openChat(page, "/c/?id=s1");
  await send(page, "write an essay");
  await host.stream("Half an ans");
  host.done({ reason: "stopped" });
  host.histories.set("s1", [
    { role: "user", text: "write an essay" },
    { role: "assistant", text: "Half an ans" },
  ]);

  const stopped = page.getByTestId("stopped-run");
  await expect(stopped).toBeVisible();
  await expect(page.getByTestId("assistant-message").first()).toContainText("Half an ans");
  await stopped.getByTestId("stopped-continue").click();
  await expect.poll(() => host.sentOfType("turn").map((f) => f.text)).toEqual([
    "write an essay",
    "Continue",
  ]);
  await host.stream("More partial");
  host.done({ reason: "stopped" });

  const turnsBefore = host.sentOfType("turn").length;
  await page.getByTestId("stopped-run").last().getByTestId("stopped-discard").click();
  await expect(page.getByTestId("turn").last()).not.toContainText("More partial");
  expect(host.sentOfType("turn")).toHaveLength(turnsBefore);

  await page.reload();
  await expect(page.getByTestId("turn").first()).toContainText("Half an ans");
});

test("day separators group turns across days, hover/focus shows time, and missing at is safe", async ({
  page,
  host,
}) => {
  await page.clock.setFixedTime(new Date("2026-11-12T14:00:00Z"));
  host.sessions = [session("s_days", "Timeline")];
  host.current = host.sessions[0]!;
  host.histories.set("s_days", [
    { role: "user", text: "Older question", at: "2026-11-09T10:15:00Z" },
    { role: "assistant", text: "Older answer", at: "2026-11-09T10:15:05Z" },
    { role: "user", text: "No timestamp row", at: null },
    { role: "assistant", text: "No timestamp answer", at: null },
    { role: "user", text: "Yesterday question", at: "2026-11-11T10:30:00Z" },
    { role: "assistant", text: "Yesterday answer", at: "2026-11-11T10:30:05Z" },
    { role: "user", text: "Today question", at: "2026-11-12T09:45:00Z" },
    { role: "assistant", text: "Today answer", at: "2026-11-12T09:45:04Z" },
  ]);

  await openChat(page, "/c/?id=s_days");
  const separators = page.getByTestId("day-separator");
  await expect(separators).toHaveCount(3);
  await expect(separators.nth(0)).toHaveText(/9 Nov 2026|Nov 9, 2026/);
  await expect(separators.nth(1)).toHaveText("Yesterday");
  await expect(separators.nth(2)).toHaveText("Today");

  const firstTime = page.getByTestId("message-time").first();
  await firstTime.focus();
  await expect(firstTime).toHaveCSS("opacity", "1");
});

test("message timing shows in detail density only", async ({ page, host }) => {
  await page.clock.install({ time: new Date("2026-11-12T10:00:00Z") });
  await openChat(page);
  await send(page, "time me");
  await page.clock.runFor(800);
  await host.stream("Timed answer");
  await page.clock.runFor(4000);
  host.done({ reason: "answered", usage: { input_tokens: 100, output_tokens: 200 } });

  await expect(page.getByTestId("message-timing")).toHaveCount(0);

  await setDensity(page, "detail");
  const timing = page.getByTestId("message-timing");
  await expect(timing).toContainText("0.8 s");
  await expect(timing).toContainText("50 tok/s");
});

test("context ring stays hidden until context arrives, turns amber above 85%, and shows 7 about segments @cross", async ({
  page,
  host,
}) => {
  await openChat(page);
  await expect(page.getByTestId("context-ring")).toHaveCount(0);

  await send(page, "fill context");
  await host.stream("Answer");
  host.done({
    reason: "answered",
    usage: { input_tokens: 110_000, output_tokens: 200 },
    context: {
      limit: 128_000,
      used: 110_000,
      segments: [
        { key: "instructions", label: "Instructions", tokens: 2000 },
        { key: "skills", label: "Skills", tokens: 1000 },
        { key: "persona", label: "Persona", tokens: 500 },
        { key: "memory", label: "Memory", tokens: 500 },
        { key: "environment", label: "Environment", tokens: 200 },
        { key: "conversation", label: "Conversation", tokens: 100_000 },
        { key: "tools", label: "Tool definitions", tokens: 5800 },
      ],
    },
  });

  const ring = page.getByTestId("context-ring");
  await expect(ring).toHaveText("86%");
  await expect(ring).toHaveAttribute("data-tone", "amber");

  await ring.click();
  const breakdown = page.getByTestId("context-breakdown");
  await expect(breakdown).toBeVisible();
  const segments = breakdown.getByTestId("context-segment");
  await expect(segments).toHaveCount(7);
  for (let i = 0; i < 7; i += 1) {
    await expect(segments.nth(i)).toContainText("about");
  }
  await page.keyboard.press("Escape");

  // Starting a new chat resets the ring; a done without context keeps it hidden.
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await page.getByTestId("new-chat").filter({ visible: true }).click();
  await expect(page.getByTestId("context-ring")).toHaveCount(0);
  await send(page, "no window");
  await host.stream("Reply");
  host.done({ reason: "answered", usage: { input_tokens: 100, output_tokens: 10 }, context: null });
  await expect(page.getByTestId("context-ring")).toHaveCount(0);
});
