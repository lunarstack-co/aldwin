import { expect, ids, openChat, send, setDensity, test } from "./fixtures/test";

test("empty home centres the composer under the greeting @cross", async ({ page }) => {
  await openChat(page);
  const home = page.getByTestId("empty-home");
  await expect(home.getByRole("heading", { name: "Ask Aldwin anything" })).toBeVisible();
  await expect(home.locator("[data-brand-mark]")).toHaveCSS("width", "40px");
  await expect(page.getByTestId("composer")).toHaveAttribute("data-variant", "centered");
  await expect(page.getByTestId(ids.input)).toBeFocused();
});

test("the home mark cycles its moods, and holds still for reduced motion", async ({ page }) => {
  await openChat(page);
  const moods = page.getByTestId("empty-home").locator("[data-mood]");
  await expect(moods).toHaveCount(4);
  const timing = await moods.evaluateAll((groups) =>
    groups.map((g) => {
      const style = getComputedStyle(g);
      return `${g.getAttribute("data-mood")} ${style.animationName} ${style.animationDuration} ${style.animationIterationCount}`;
    }),
  );
  expect(timing).toEqual([
    "open mark-open 7s infinite",
    "closed mark-closed 7s infinite",
    "happy mark-happy 7s infinite",
    "wink mark-wink 7s infinite",
  ]);
  // Only the home mark moves; the sidebar's stays still.
  await expect(page.locator("[data-brand-mark][data-animated]")).toHaveCount(1);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect.poll(() => moods.evaluateAll((groups) => groups.map((g) => getComputedStyle(g).opacity))).toEqual(["1", "0", "0", "0"]);
});

test("first send docks the composer and lays out bubbles @cross", async ({ page, host }) => {
  host.answerNext("Hi there.");
  await openChat(page);
  await send(page, "hello");
  await expect(page.getByTestId("assistant-message")).toContainText("Hi there.");
  await expect(page.getByTestId("composer")).toHaveAttribute("data-variant", "docked");
  const viewport = page.viewportSize()!;
  const box = (await page.getByTestId("composer").boundingBox())!;
  expect(viewport.height - (box.y + box.height)).toBeLessThan(48);
  const bubble = page.getByTestId("user-message").locator("> div");
  await expect(bubble).toHaveCSS("border-radius", "16px");
  const column = (await page.getByTestId("transcript").locator("section").boundingBox())!;
  const bubbleBox = (await bubble.boundingBox())!;
  expect(Math.abs(column.x + column.width - (bubbleBox.x + bubbleBox.width))).toBeLessThan(4);
});

test("enter sends and shift enter adds a line", async ({ page, host }) => {
  await openChat(page);
  const input = page.getByTestId(ids.input);
  await input.pressSequentially("a");
  await input.press("Shift+Enter");
  await input.pressSequentially("b");
  await input.press("Enter");
  await expect.poll(() => host.sentOfType("turn").length).toBe(1);
  expect(host.sentOfType("turn")[0]).toMatchObject({ text: "a\nb", skills: [] });
});

test("whitespace is not a message", async ({ page, host }) => {
  await openChat(page);
  await page.getByTestId(ids.input).fill("   ");
  await expect(page.getByTestId(ids.send)).toBeDisabled();
  await page.getByTestId(ids.input).press("Enter");
  expect(host.sentOfType("turn")).toHaveLength(0);
});

test("stop from the button and from esc", async ({ page, host }) => {
  await openChat(page);
  await send(page, "first");
  await expect(page.getByTestId(ids.stop)).toBeVisible();
  await page.getByTestId(ids.stop).click();
  await expect(page.getByTestId(ids.send)).toBeVisible();
  expect(host.sentOfType("stop")).toHaveLength(1);
  await expect(page.getByTestId(ids.turn).last()).toContainText("Stopped before the answer was finished.");

  await send(page, "second");
  await expect(page.getByTestId(ids.stop)).toBeVisible();
  await page.getByTestId(ids.input).press("Escape");
  await expect.poll(() => host.sentOfType("stop").length).toBe(2);
});

test("200 deltas stream into one answer and stay pinned @cross", async ({ page, host }) => {
  await openChat(page);
  await send(page, "tell me");
  await expect(page.getByTestId(ids.stop)).toBeVisible();
  const text = "word ".repeat(160).trim() + ".";
  await host.stream(text, { chunkSize: 4, delayMs: 2 });
  await expect(page.getByTestId("assistant-message")).toHaveAttribute("aria-busy", "true");
  host.done();
  await expect(page.getByTestId("assistant-message")).not.toHaveAttribute("aria-busy", "true");
  await expect(page.getByTestId("assistant-message")).toContainText(text.slice(-20));
  const pinned = await page.getByTestId("transcript").evaluate((el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 4);
  expect(pinned).toBe(true);
});

test("scrolling up shows the jump button", async ({ page, host }) => {
  host.answerNext("line\n\n".repeat(80));
  await openChat(page);
  await send(page, "long");
  await expect(page.getByTestId("message-actions")).toBeVisible();
  await page.getByTestId("transcript").evaluate((el) => el.scrollTo({ top: 0 }));
  const jump = page.getByRole("button", { name: "Scroll to the newest message" });
  await expect(jump).toBeVisible();
  await jump.click();
  await expect
    .poll(() => page.getByTestId("transcript").evaluate((el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 4))
    .toBe(true);
});

test("a running turn shows one working line, with no stray dot and nothing in the composer", async ({ page, host }) => {
  await openChat(page);
  await send(page, "research");
  host.toolCall({ name: "web_search" });
  const turn = page.getByTestId("turn").last();
  await expect(turn.getByText(/Working for \d+s/)).toHaveCount(1);
  await expect(page.getByTestId("thinking")).toHaveCount(0);
  await expect(turn.locator(".aui-md")).toHaveCount(0);
  await expect(page.getByTestId("composer")).not.toContainText("Working");
  host.liveStatus("Reading the page");
  await expect(turn.getByText(/Reading the page · \d+s/)).toBeVisible();
  await host.stream("Found it.");
  await expect(turn.getByText(/Working for|Reading the page/)).toHaveCount(1);
  host.done();
  await expect(turn).toContainText(/Worked for \d+s/);
});

test("thinking shows until the first words", async ({ page, host }) => {
  await openChat(page);
  await send(page, "think");
  const thinking = page.getByTestId("thinking");
  await expect(thinking).toContainText(/Working for \ds/);
  host.liveStatus("Searching the web", "web_search");
  await expect(thinking).toContainText("Searching the web");
  await expect(thinking.getByText("web_search")).toHaveCSS("font-family", /jet ?brains ?mono/i);
  await host.stream("Here.");
  await expect(thinking).toHaveCount(0);
});

test("copy puts the answer on the clipboard", async ({ page, host, context, browserName }) => {
  test.skip(browserName !== "chromium", "clipboard permissions are Chromium-only");
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  host.answerNext("**Done**");
  await openChat(page);
  await send(page, "q");
  await page.getByTestId("copy-answer").click();
  await expect(page.getByTestId("copy-answer")).toContainText("Copied");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("**Done**");
});

test("retry adds a turn with the last question", async ({ page, host }) => {
  host.answerNext("One.");
  await openChat(page);
  await send(page, "q1");
  await expect(page.getByTestId(ids.turn)).toHaveCount(1);
  host.answerNext("Two.");
  await page.getByTestId("retry-turn").click();
  await expect(page.getByTestId(ids.turn)).toHaveCount(2);
  expect(host.sentOfType("turn").map((f) => f.text)).toEqual(["q1", "q1"]);
  await expect(page.getByTestId(ids.turn).first()).toContainText("One.");
});

test("the action bar has exactly copy and retry", async ({ page, host }) => {
  host.answerNext("Fine.");
  await openChat(page);
  await send(page, "q");
  const bar = page.getByTestId("message-actions");
  await expect(bar).toBeVisible();
  await expect(bar.getByRole("button")).toHaveCount(2);
  await expect(bar.getByRole("button").nth(0)).toContainText("Copy");
  await expect(bar.getByRole("button").nth(1)).toContainText("Retry");
});

test("footer shows usage in detail only", async ({ page, host }) => {
  host.answerNext("Counted.");
  await openChat(page);
  await send(page, "q");
  await expect(page.getByTestId("assistant-message")).toContainText("Counted.");
  await expect(page.getByTestId("turn-footer")).toHaveCount(0);
  await setDensity(page, "detail");
  await expect(page.getByTestId("turn-footer")).toHaveText(/^1,234 in · 567 out · \d+ s$/);
  await expect(page.getByTestId("turn-footer")).toHaveCSS("font-variant-numeric", "tabular-nums");
});

test("no usage hides the footer", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "q");
  await host.stream("Answer.");
  host.done({ usage: null });
  await expect(page.getByTestId("assistant-message")).toContainText("Answer.");
  await expect(page.getByTestId("turn-footer")).toHaveCount(0);
});
