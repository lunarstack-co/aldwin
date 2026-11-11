import { expect, openChat, send, test } from "./fixtures/test";

test("clean shows one live chip, then worked for @cross", async ({ page, host }) => {
  await page.clock.install({ time: new Date("2026-11-11T10:00:00") });
  await openChat(page);
  await send(page, "q");
  host.toolCall({ call_id: "c0", name: "a" });
  host.toolResult({ call_id: "c0", ok: true });
  host.toolCall({ call_id: "c1", name: "b" });
  host.toolResult({ call_id: "c1", ok: true });
  host.toolCall({ call_id: "c2", name: "c" });
  await page.clock.runFor(8000);
  const chip = page.getByTestId("tool-group");
  await expect(chip).toHaveCount(1);
  await expect(chip).toHaveAttribute("data-working", "true");
  // The fake clock keeps running between steps, so allow a second either way.
  await expect(chip.getByRole("button").first()).toContainText(/Working for [89]s/);
  await expect(page.getByTestId("tool-card")).toHaveCount(0);
  host.toolResult({ call_id: "c2", ok: false });
  await page.clock.runFor(4000);
  await host.stream("Answer.");
  host.done();
  const trigger = chip.getByRole("button").first();
  await expect(trigger).toContainText(/Worked for 1[23]s/);
  await expect(trigger).toContainText("· 1 failed");
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByTestId("tool-card")).toHaveCount(3);
});

test("text only turns have no chip", async ({ page, host }) => {
  host.answerNext("Just text.");
  await openChat(page);
  await send(page, "q");
  await expect(page.getByTestId("assistant-message")).toContainText("Just text.");
  await expect(page.getByTestId("tool-group")).toHaveCount(0);
});

test("history tools show a count", async ({ page, host }) => {
  host.history = [
    { role: "user", text: "Find it" },
    { role: "tool", text: "✓ grep" },
    { role: "tool", text: "✓ read_file" },
    { role: "assistant", text: "Found." },
  ];
  await openChat(page);
  const chip = page.getByTestId("tool-group");
  await expect(chip.getByRole("button").first()).toContainText("2 tool calls");
  await expect(chip).not.toContainText("0s");
});
