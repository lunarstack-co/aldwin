import type { Page } from "@playwright/test";

import { expect, openChat, send, test } from "./fixtures/test";
import type { FakeHost } from "./fixtures/fake-host";

const CONTEXT = {
  limit: 128_000,
  used: 90_000,
  segments: [
    { key: "instructions", label: "Instructions", tokens: 6_000 },
    { key: "conversation", label: "Conversation", tokens: 84_000 },
  ],
};

async function answerWithRing(page: Page, host: FakeHost) {
  await openChat(page);
  await send(page, "a long chat");
  await host.stream("Answer");
  host.done({ reason: "answered", context: CONTEXT });
  await page.getByTestId("context-ring").click();
}

test("Make room now asks the host and finishes without a toast", async ({ page, host }) => {
  await answerWithRing(page, host);
  const button = page.getByTestId("context-make-room");
  await expect(button).toHaveText("Make room now");
  await expect(page.getByText("Your messages stay as they are.")).toBeVisible();

  await button.click();

  await expect(page.getByTestId("context-ring")).toHaveAttribute("aria-label", /\(35%\)/);
  expect(host.sent.filter((frame) => frame.type === "compact")).toHaveLength(1);
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
});

test("Make room now explains a short chat", async ({ page, host }) => {
  host.compactError = {
    code: "invalid_request",
    message: "This chat is still short, so there's nothing to make room for yet.",
  };
  await answerWithRing(page, host);

  await page.getByTestId("context-make-room").click();

  await expect(page.getByTestId("context-make-room-error")).toHaveText(
    "This chat is still short, so there's nothing to make room for yet.",
  );
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
});

test("Make room now waits while Aldwin is answering", async ({ page, host }) => {
  await answerWithRing(page, host);
  await page.keyboard.press("Escape");
  await send(page, "another question");
  await host.stream("Thinking");

  await page.getByTestId("context-ring").click();

  await expect(page.getByTestId("context-make-room")).toBeDisabled();
});

test("the ring stays after a reload and follows Make room now", async ({ page, host }) => {
  await answerWithRing(page, host);
  host.history = [
    { role: "user", text: "a long chat" },
    { role: "assistant", text: "Answer" },
  ];

  await page.reload();
  const ring = page.getByTestId("context-ring");
  await expect(ring).toBeVisible();
  await expect(ring).toHaveAttribute("aria-label", /70%/);

  await ring.click();
  await page.getByTestId("context-make-room").click();
  await expect(ring).toHaveAttribute("aria-label", /35%/);
});

test("a new chat has no ring until its first answer", async ({ page, host }) => {
  await answerWithRing(page, host);
  await page.keyboard.press("Escape");

  await page.getByTestId("new-chat").first().click();

  await expect(page.getByTestId("context-ring")).toHaveCount(0);
});

test("Make room now brings its progress into view when the chat is scrolled up", async ({ page, host }) => {
  host.holdCompact = true;
  await openChat(page);
  await send(page, "a long chat");
  await host.stream(Array.from({ length: 80 }, (_, i) => `Line ${i + 1} of a long answer.`).join("\n\n"));
  host.done({ reason: "answered", context: CONTEXT });
  const transcript = page.getByTestId("transcript");
  await transcript.evaluate((el) => el.scrollTo(0, 0));
  await page.getByTestId("context-ring").click();
  await page.getByTestId("context-make-room").click();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("making-room")).toBeInViewport();
  host.release();
  await expect(page.getByTestId("making-room")).toHaveCount(0);
});

test("while room is made the chat says so, typing goes on and sending waits", async ({ page, host }) => {
  host.holdCompact = true;
  await answerWithRing(page, host);

  await page.getByTestId("context-make-room").click();

  await expect(page.getByTestId("context-make-room")).toHaveText("Making room…");
  await page.keyboard.press("Escape");
  const row = page.getByTestId("making-room");
  await expect(row).toBeVisible();
  await expect(row).toContainText("Writing notes on earlier messages to make room");

  const input = page.getByTestId("composer-input");
  await input.fill("next question");
  await input.press("Enter");
  await expect(input).toHaveValue("next question");
  const send = page.getByTestId("composer-send");
  await expect(send).toHaveAttribute("aria-disabled", "true");
  await send.hover();
  await expect(page.getByTestId("composer-send-wait")).toHaveText(
    "Aldwin is making room in this chat. You can send once it's done.",
  );
  await send.click({ force: true });
  expect(host.sent.filter((frame) => frame.type === "turn")).toHaveLength(1);

  host.release();

  await expect(row).toHaveCount(0);
  await expect(page.getByTestId("composer-send")).not.toHaveAttribute("aria-disabled");
  await input.press("Enter");
  await expect.poll(() => host.sent.filter((frame) => frame.type === "turn").length).toBe(2);
});

test("below the line Aldwin makes room at, Make room now waits and says why", async ({ page, host }) => {
  await openChat(page);
  await send(page, "a short chat");
  await host.stream("Answer");
  host.done({ reason: "answered", context: { ...CONTEXT, used: 19_200, compact_at: 51_200 } });
  await page.getByTestId("context-ring").click();

  const button = page.getByTestId("context-make-room");
  await expect(button).toHaveAttribute("aria-disabled", "true");
  await button.hover();
  await expect(page.getByTestId("context-make-room-not-yet")).toHaveText(
    "You can make room once this chat passes 40% of what the model can read. It's at 15% now, so there's plenty of room.",
  );
  await button.click({ force: true });
  expect(host.sent.filter((frame) => frame.type === "compact")).toHaveLength(0);
});

test("past the line Make room now is ready", async ({ page, host }) => {
  await openChat(page);
  await send(page, "a long chat");
  await host.stream("Answer");
  host.done({ reason: "answered", context: { ...CONTEXT, compact_at: 51_200 } });
  await page.getByTestId("context-ring").click();

  await expect(page.getByTestId("context-make-room")).toBeEnabled();
  await expect(page.getByTestId("context-make-room")).not.toHaveAttribute("aria-disabled");
});

test("a message that makes room says so, then carries on", async ({ page, host }) => {
  await openChat(page);
  await send(page, "keep going");
  const thinking = page.getByTestId("thinking");
  host.liveStatus("Making room in this chat...");
  await expect(thinking).toContainText("Making room in this chat...");
  host.liveStatus("");
  await expect(thinking).not.toContainText("Making room");
  await expect(thinking).toContainText(/Working for \ds/);
  host.notice("Made room by shortening long results from earlier steps. Your messages are all still here.", "room");
  await host.stream("Done.");
  host.done({ reason: "answered" });
  await expect(page.getByTestId("assistant-message").last()).toContainText("Done.");
  await expect(page.locator("[data-sonner-toast]")).toHaveCount(0);
});

test("other notices still show as a toast", async ({ page, host }) => {
  await openChat(page);
  host.notice("skills reloaded");
  await expect(page.locator("[data-sonner-toast]")).toContainText("skills reloaded");
});
