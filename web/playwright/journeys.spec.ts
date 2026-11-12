import { expect, send, test } from "./fixtures/test";
import { sessions25 } from "./fixtures/seeds";

test("new user: from no keys to a first answer @cross", async ({ page, host }) => {
  host.status = { ...host.status, keys_ok: false, host_ready: false, profile_done: false };
  let saved = false;
  host.rest.set("POST /v1/setup/provider-key", async (route) => {
    saved = true;
    host.status = { ...host.status, keys_ok: true, host_ready: true };
    await route.fulfill({ json: { masked: "x" } });
  });
  await page.goto("/");
  await expect(page).toHaveURL(/\/onboarding\/connect\/$/);
  await page.getByRole("textbox", { name: "API key" }).fill("sk-good");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  for (const path of ["name", "occupation", "personality"]) {
    await expect(page).toHaveURL(new RegExp(`/onboarding/${path}/$`));
    if (path === "personality") break;
    await page.getByRole("button", { name: "Continue" }).click();
  }
  await page.getByRole("button", { name: "Skip for now" }).click();
  expect(saved).toBe(true);
  await expect(page.getByTestId("composer")).toHaveAttribute("data-variant", "centered");
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-phase", "open");
  const ticket = host.calls.findIndex((c) => c.path === "/v1/ws-ticket");
  expect(ticket).toBeGreaterThan(host.calls.findIndex((c) => c.path === "/v1/setup/provider-key"));
  host.onTurn = async (frame) => {
    host.emit({ re: frame.id, kind: "tool_call", call_id: "c1", name: "search", args_preview: "", summary: "" });
    host.emit({ re: frame.id, kind: "tool_result", call_id: "c1", ok: true, summary: "", preview: "", duration_ms: 10 });
    host.emit({ re: frame.id, kind: "text", delta: "Partly" });
    host.activeTurn = null;
    host.emit({ re: frame.id, kind: "error", code: "provider_error", message: "Model unavailable" });
  };
  await send(page, "first question");
  await expect(page.getByTestId("composer")).toHaveAttribute("data-variant", "docked");
  await expect(page.getByTestId("tool-group")).toHaveCount(1);
  await expect(page.getByTestId("error-row")).toContainText("Model unavailable");
  host.answerNext("Second time lucky.");
  await page.getByTestId("retry-error").click();
  await expect(page.getByTestId("turn")).toHaveCount(2);
  expect(host.sentOfType("turn").map((f) => f.text)).toEqual(["first question", "first question"]);
});

test("returning user: history, resume, new chat, detail, stop @cross", async ({ page, host }) => {
  host.sessions = sessions25();
  await page.goto("/");
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Groceries" }).click();
  await expect(page).toHaveURL(/\?id=s03$/);
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Groceries.");
  host.answerNext("More groceries.");
  await send(page, "add milk");
  await expect(page.getByTestId("assistant-message").last()).toContainText("More groceries.");
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await page.getByTestId("new-chat").filter({ visible: true }).click();
  await expect(page.getByTestId("empty-home")).toBeVisible();
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await expect(page.getByTestId("new-chat").filter({ visible: true })).toBeDisabled();
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.keyboard.press("Escape");
  expect(host.sentOfType("sessions.new")).toHaveLength(1);
  await page.getByTestId("density-toggle").click();
  await page.getByTestId("density-detail").click();
  await page.keyboard.press("Escape");
  host.onTurn = null;
  await send(page, "long task");
  host.toolCall({ name: "grep" });
  await expect(page.getByTestId("tool-card")).toBeVisible();
  await page.getByTestId("composer-stop").click();
  await expect(page.getByTestId("turn").last()).toContainText("Stopped before the answer was finished.");
});
