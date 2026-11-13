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
  const bubble = page.getByTestId("user-bubble");
  await expect(bubble).toHaveCSS("border-radius", "16px");
  const column = (await page.getByTestId("transcript").locator("section").boundingBox())!;
  const bubbleBox = (await bubble.boundingBox())!;
  expect(Math.abs(column.x + column.width - (bubbleBox.x + bubbleBox.width))).toBeLessThan(4);
});

test("a one-line disclaimer sits above the composer, before and after the first message", async ({ page }) => {
  await openChat(page);
  const disclaimer = page.getByTestId("composer-disclaimer");
  await expect(disclaimer).toHaveText("Aldwin can make mistakes. Check important answers.");
  const above = async () => {
    const [d, c] = await Promise.all([disclaimer.boundingBox(), page.getByTestId("composer").boundingBox()]);
    expect(d!.y + d!.height).toBeLessThanOrEqual(c!.y);
  };
  await above();
  await send(page, "hello");
  await expect(disclaimer).toBeVisible();
  await above();
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
  // The answer renders in the background; wait for all of it so the page stops growing.
  await expect(page.getByTestId("assistant-message").locator("p")).toHaveCount(80);
  const jump = page.getByRole("button", { name: "Scroll to the newest message" });
  // The thread may still be settling at the bottom; scroll up until it stays there.
  await expect(async () => {
    await page.getByTestId("transcript").evaluate((el) => el.scrollTo({ top: 0 }));
    await expect(jump).toBeVisible({ timeout: 500 });
  }).toPass();
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

test("hovering an earlier answer shows its actions without moving anything @cross", async ({ page, host }) => {
  host.answerNext("First answer.");
  await openChat(page);
  await send(page, "q1");
  await expect(page.getByTestId("message-actions")).toHaveCount(1);
  host.answerNext("Second answer.");
  await send(page, "q2");
  const bars = page.getByTestId("message-actions");
  await expect(bars).toHaveCount(2);
  const earlier = bars.first();
  const canHover = await page.evaluate(() => matchMedia("(hover: hover)").matches);
  // Touch screens cannot hover, so the bar stays visible there and there is nothing to reveal.
  if (!canHover) {
    await expect(earlier).toHaveCSS("opacity", "1");
    return;
  }
  await expect(earlier).toHaveCSS("opacity", "0");
  await expect(bars.last()).toHaveCSS("opacity", "1");
  // Hover scrolls the answer into view, so compare layout, not viewport positions.
  const layout = () =>
    page.evaluate(() => {
      const first = document.querySelector("[data-testid=turn]")!.getBoundingClientRect();
      const below = [...document.querySelectorAll("[data-testid=user-message]")].at(-1)!.getBoundingClientRect();
      return { gap: below.top - first.top, height: first.height };
    });
  // After a send the newest question is scrolled to the top; wait for that, then scroll back up to the first answer.
  const transcript = page.getByTestId("transcript");
  // That scroll is smooth, so wait until it has stopped moving before scrolling back.
  const settled = () =>
    transcript.evaluate(async (el) => {
      const first = el.scrollTop;
      await new Promise((done) => setTimeout(done, 200));
      return first > 0 && el.scrollTop === first;
    });
  await expect.poll(settled).toBe(true);
  await transcript.evaluate((el) => el.scrollTo({ top: 0 }));
  await expect.poll(() => transcript.evaluate((el) => el.scrollTop)).toBe(0);
  await expect(page.getByTestId("turn").first()).toBeInViewport();
  const before = await layout();
  const box = (await page.getByTestId("turn").first().boundingBox())!;
  await page.mouse.move(box.x + 20, box.y + box.height / 2);
  await expect(earlier).toHaveCSS("opacity", "1");
  expect(await layout()).toEqual(before);
  // Keyboard users reach the buttons too: focus reveals the bar.
  await page.mouse.move(0, 0);
  await earlier.getByTestId("copy-answer").focus();
  await expect(earlier).toHaveCSS("opacity", "1");
});
