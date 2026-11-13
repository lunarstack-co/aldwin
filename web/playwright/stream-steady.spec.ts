import type { Page } from "@playwright/test";

import { expect, openChat, send, setDensity, test } from "./fixtures/test";

const FIRST = "Let me look at your project board first.";
const ANSWER = "Your board has three open issues, all assigned to you.";

/** Records every time the turn's visible text gets shorter or its text element is replaced. */
async function watch(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __shrinks: string[]; __swaps: number; __last: string; __node: Element | null };
    w.__shrinks = [];
    w.__swaps = 0;
    w.__last = "";
    w.__node = null;
    const check = () => {
      const turns = document.querySelectorAll("[data-testid=turn]");
      const turn = turns[turns.length - 1];
      if (!turn) return;
      // Only the written text, not the work chip's ticking label.
      const text = [...turn.querySelectorAll(".aui-md")].map((el) => el.textContent ?? "").join(" ");
      // The first sentence, once shown, must never shrink or vanish.
      const shown = text.includes("Let me") ? text.slice(text.indexOf("Let me")) : "";
      if (w.__last && !shown.startsWith(w.__last.slice(0, Math.min(w.__last.length, 30)))) {
        w.__shrinks.push(`${JSON.stringify(w.__last.slice(0, 30))} -> ${JSON.stringify(shown.slice(0, 30))}`);
      }
      if (shown) w.__last = shown;
      const node = turn.querySelector(".aui-md p");
      if (node && w.__node && node !== w.__node && w.__node.isConnected === false) w.__swaps += 1;
      if (node) w.__node = node;
    };
    new MutationObserver(check).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
}

async function report(page: Page) {
  return page.evaluate(() => {
    const w = window as unknown as { __shrinks: string[]; __swaps: number };
    return { shrinks: w.__shrinks, swaps: w.__swaps };
  });
}

for (const density of ["clean", "detail"] as const) {
  test(`streamed text stays put while Aldwin works, in ${density} @cross`, async ({ page, host }) => {
    await openChat(page);
    await setDensity(page, density);
    host.onTurn = null;
    await send(page, "what's on my board?");
    await expect.poll(() => host.sentOfType("turn").length).toBe(1);
    await watch(page);
    await host.stream(FIRST, { chunkSize: 4, delayMs: 15 });
    await expect(page.getByTestId("turn").last()).toContainText(FIRST);
    host.toolCall({ call_id: "c1", name: "list_issues" });
    for (let i = 0; i < 6; i += 1) {
      host.liveStatus(`Working (${i + 1})`, "list_issues");
      await page.waitForTimeout(120);
    }
    host.toolResult({ call_id: "c1", summary: "3 issues" });
    host.toolCall({ call_id: "c2", name: "read_issue" });
    host.toolResult({ call_id: "c2", summary: "read" });
    await page.waitForTimeout(400);
    await host.stream(` ${ANSWER}`, { chunkSize: 4, delayMs: 15 });
    host.done();
    await expect(page.getByTestId("turn").last()).toContainText(ANSWER);
    // The first sentence stays above the work and the answer below it, in the order they streamed.
    const order = await page.getByTestId("turn").last().evaluate((turn) => {
      const text = turn.textContent ?? "";
      return [text.indexOf("Let me look"), text.indexOf("Your board")];
    });
    expect(order[0]).toBeGreaterThanOrEqual(0);
    expect(order[0]).toBeLessThan(order[1]);
    await page.waitForTimeout(300);
    const { shrinks, swaps } = await report(page);
    expect(shrinks, "the first sentence disappeared or restarted").toEqual([]);
    expect(swaps, "the streamed paragraph was thrown away and drawn again").toBe(0);
  });
}

test("text between two rounds of work keeps its place, and only the last chip is timed", async ({ page, host }) => {
  await openChat(page);
  host.onTurn = null;
  await send(page, "plan my week");
  await expect.poll(() => host.sentOfType("turn").length).toBe(1);
  host.toolCall({ call_id: "a1", name: "list_events" });
  host.toolResult({ call_id: "a1", summary: "8 events" });
  await host.stream("You have eight events.");
  host.toolCall({ call_id: "b1", name: "list_tasks" });
  host.toolResult({ call_id: "b1", summary: "4 tasks" });
  host.toolCall({ call_id: "b2", name: "read_task" });
  host.toolResult({ call_id: "b2", summary: "read" });
  await host.stream(" Here is a plan.");
  host.done();
  const turn = page.getByTestId("turn").last();
  await expect(turn).toContainText("Here is a plan.");
  const chips = turn.getByTestId("tool-group");
  await expect(chips).toHaveCount(2);
  await expect(chips.first()).toContainText("1 tool call");
  await expect(chips.last()).toContainText(/Worked for \d+s/);
  const text = (await turn.textContent()) ?? "";
  expect(text.indexOf("You have eight events.")).toBeLessThan(text.indexOf("Here is a plan."));
  expect(text.indexOf("1 tool call")).toBeLessThan(text.indexOf("You have eight events."));
});
