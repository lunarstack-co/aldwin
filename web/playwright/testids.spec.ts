import { expect, openChat, send, test } from "./fixtures/test";

const KEPT = [
  "aldwin-chat-shell", "aldwin-gates-clear", "assistant-message", "composer", "composer-input",
  "model-picker-composer", "composer-send", "composer-skills", "composer-stop", "connection-status", "copy-answer",
  "copy-error", "density-toggle", "empty-home", "error-row", "history-eyebrow", "message-actions", "new-chat",
  "retry-error", "retry-turn", "see-all", "settings", "tool-card", "tool-group", "transcript", "turn", "user-message",
];

test("every kept test id is reachable", async ({ page, host }) => {
  const seen = new Set<string>();
  const collect = async () => {
    for (const id of await page.locator("[data-testid]").evaluateAll((els) => els.map((el) => el.getAttribute("data-testid")!))) seen.add(id);
  };
  await openChat(page);
  await collect();
  await send(page, "q");
  host.toolCall();
  await page.getByTestId("tool-group").getByRole("button").first().click();
  await expect(page.getByTestId("tool-card")).toBeVisible();
  await collect();
  await host.stream("x");
  host.toolResult();
  host.error("x", "failed");
  await expect(page.getByTestId("error-row")).toBeVisible();
  await collect();
  host.answerNext("ok");
  await page.getByTestId("retry-error").click();
  await expect(page.getByTestId("message-actions").last()).toBeVisible();
  await collect();
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await collect();
  expect(KEPT.filter((id) => !seen.has(id))).toEqual([]);
  expect(seen.has("history-see-all") && seen.has("history-search") && seen.has("see-all-back")).toBe(true);
});
