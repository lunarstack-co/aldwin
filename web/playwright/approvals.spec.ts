import { existsSync } from "node:fs";
import { join } from "node:path";

import type { Page } from "@playwright/test";

import type { FakeHost } from "./fixtures/fake-host";
import { expect, ids, openChat, send, setDensity, test } from "./fixtures/test";

test("terminal card with risky label, locked composer, pill, and allow transition @cross", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "free up space");
  host.toolCall({ call_id: "c1", name: "run_command", args_preview: "rm -rf ~/x" });
  host.emitApproval({
    approval_id: "ap_rm",
    call_id: "c1",
    tool: "run_command",
    tier: "ask",
    summary: "Run command in ws: rm -rf ~/x",
    grantable: false,
    digest: "sha256:rm1",
    risky: ["recursive delete"],
    display: {
      kind: "terminal",
      command: "rm -rf ~/x",
      cwd: "/ws",
      stdout: "",
      stderr: "",
      exit_code: null,
      duration_ms: 0,
      truncated: false,
    },
  });

  const card = page.getByTestId("approval-card");
  await expect(card).toContainText("Run a command");
  await expect(card.getByTestId("display-terminal")).toContainText("rm -rf ~/x");
  await expect(card.getByTestId("approval-risky")).toContainText("recursive delete");
  await expect(card.getByRole("button", { name: "Allow", exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: "Deny", exact: true })).toBeVisible();
  await expect(card.getByRole("button", { name: /Always allow/ })).toHaveCount(0);
  await expect(page.getByTestId(ids.input)).toBeDisabled();
  await expect(page.getByTestId("approval-pill")).toHaveText("Aldwin needs your OK");

  await card.getByRole("button", { name: "Allow", exact: true }).click();
  await expect.poll(() => host.sentOfType("approve")).toEqual([
    expect.objectContaining({
      type: "approve",
      approval_id: "ap_rm",
      answer: "yes",
      digest: "sha256:rm1",
    }),
  ]);
  await expect(card).toContainText("Running");
  await expect(card.getByRole("button", { name: "Allow", exact: true })).toHaveCount(0);

  host.emitToolResult({
    call_id: "c1",
    ok: true,
    summary: "exit 0",
    duration_ms: 45,
    display: {
      kind: "terminal",
      command: "rm -rf ~/x",
      cwd: "/ws",
      stdout: "cleaned\n",
      stderr: "",
      exit_code: 0,
      duration_ms: 45,
      truncated: false,
    },
  });
  await expect(card).toContainText("Approved");
  await expect(card).toContainText("cleaned");
});

test("grantable diff card supports Always allow in this chat and labels diff lines", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "edit notes");
  host.toolCall({ call_id: "c1", name: "edit_file" });
  host.emitApproval({
    approval_id: "ap_diff",
    call_id: "c1",
    tool: "edit_file",
    tier: "diff",
    summary: "Edit notes.md (+3 −1)",
    grantable: true,
    grant_scope: "file",
    digest: "sha256:diff1",
    display: {
      kind: "diff",
      path: "notes.md",
      additions: 3,
      deletions: 1,
      lines: [
        { kind: "removed", text: "old" },
        { kind: "added", text: "a" },
        { kind: "added", text: "b" },
        { kind: "added", text: "c" },
      ],
      truncated: false,
    },
  });

  const card = page.getByTestId("approval-card");
  await expect(card).toContainText("notes.md");
  await expect(card).toContainText("+3");
  await expect(card).toContainText("−1");
  await expect(card).toContainText("Added line:");
  await expect(card).toContainText("Removed line:");

  await card.getByRole("button", { name: "Always allow in this chat" }).click();
  await expect.poll(() => host.sentOfType("approve").at(-1)).toMatchObject({
    type: "approve",
    approval_id: "ap_diff",
    answer: "yes_session",
    digest: "sha256:diff1",
  });
});

test("deny with and without a reason, capping reason at 280 characters", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "first");
  host.toolCall({ call_id: "c1", name: "run_command" });
  host.emitApproval({ approval_id: "ap_d1", call_id: "c1", tool: "run_command", summary: "Run a", digest: "sha256:1" });

  const card1 = page.getByTestId("approval-card");
  await card1.getByRole("button", { name: "Deny" }).click();
  const reasonInput = card1.getByTestId("approval-deny-reason");
  await reasonInput.fill("not that folder");
  await card1.getByTestId("approval-deny-confirm").click();

  await expect.poll(() => host.sentOfType("approve")[0]).toMatchObject({
    approval_id: "ap_d1",
    answer: "no",
    reason: "not that folder",
  });
  await expect(card1).toContainText("Denied: you said no");
  host.done();

  await send(page, "second");
  host.toolCall({ call_id: "c2", name: "run_command" });
  host.emitApproval({ approval_id: "ap_d2", call_id: "c2", tool: "run_command", summary: "Run b", digest: "sha256:2" });

  const card2 = page.locator('[data-approval-id="ap_d2"]');
  await card2.getByRole("button", { name: "Deny" }).click();
  await card2.getByTestId("approval-deny-confirm").click();
  await expect.poll(() => host.sentOfType("approve").length).toBe(2);
  expect("reason" in host.sentOfType("approve")[1]!).toBe(false);
  await expect(card2).toContainText("Denied: you said no");
  host.done();

  await send(page, "third");
  host.toolCall({ call_id: "c3", name: "run_command" });
  host.emitApproval({ approval_id: "ap_d3", call_id: "c3", tool: "run_command", summary: "Run c", digest: "sha256:3" });

  const card3 = page.locator('[data-approval-id="ap_d3"]');
  await card3.getByRole("button", { name: "Deny" }).click();
  await card3.getByTestId("approval-deny-reason").fill("z".repeat(300));
  await expect(card3.getByTestId("approval-deny-reason")).toHaveValue("z".repeat(280));
  await card3.getByTestId("approval-deny-confirm").click();
  await expect.poll(() => String(host.sentOfType("approve")[2]?.reason ?? "").length).toBe(280);
});

test("permission grant card shows capability, requester and reach @cross", async ({ page, host }) => {
  await openChat(page);
  await send(page, "read tax");
  host.toolCall({ call_id: "c1", name: "read_file" });
  host.emitApproval({
    approval_id: "ap_grant",
    call_id: "c1",
    tool: "read_file",
    tier: "ask",
    summary: "Read outside workspace: ~/Documents/tax (this folder, this session)?",
    grantable: true,
    grant_scope: "folder",
    digest: "sha256:g1",
    display: {
      kind: "grant",
      scope: "folder",
      reach: ["~/Documents/tax"],
    },
  });

  const card = page.getByTestId("approval-card");
  await expect(card).toContainText("Read files in ~/Documents/tax");
  await expect(card).toContainText("Aldwin");
  await expect(card).toContainText("~/Documents/tax");

  await card.getByRole("button", { name: "For this chat" }).click();
  await expect.poll(() => host.sentOfType("approve").at(-1)).toMatchObject({
    approval_id: "ap_grant",
    answer: "yes_session",
    digest: "sha256:g1",
  });
});

const linearApproval = {
  approval_id: "ap_nocall",
  call_id: null,
  tool: "mcp__linear__save_issue",
  summary: "Linear wants to run save_issue",
  detail: 'title: "Ship 0.0.6"',
  connector_title: "Linear",
  grantable: true,
  digest: "sha256:lin1",
  display: null,
};

async function waitOnLinearApproval(page: Page, host: FakeHost) {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "create linear ticket");
  host.emitApproval(linearApproval);
  const card = page.getByTestId("approval-card");
  await expect(card).toBeVisible();
  return card;
}

test("a card with no display shows its detail, names the app, takes focus and ignores Escape", async ({ page, host }) => {
  const card = await waitOnLinearApproval(page, host);
  await expect(card).toContainText("in Linear");
  await expect(card.getByTestId("approval-detail")).toContainText('title: "Ship 0.0.6"');
  await expect(card.getByRole("button", { name: "Allow", exact: true })).toBeFocused();

  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");
  expect(host.sentOfType("approve")).toHaveLength(0);
  expect(host.sentOfType("stop")).toHaveLength(0);
  await expect(card.getByRole("button", { name: "Allow", exact: true })).toBeVisible();
});

test("the pill scrolls the waiting card into view", async ({ page, host }) => {
  const card = await waitOnLinearApproval(page, host);
  await page.getByTestId("approval-pill").click();
  await expect(card).toBeInViewport();
});

test("a re-ask after the digest changes replaces the waiting card", async ({ page, host }) => {
  const card = await waitOnLinearApproval(page, host);
  host.emitApproval({
    ...linearApproval,
    approval_id: "ap_nocall_b",
    summary: "Linear wants to run save_issue (updated)",
    detail: 'title: "Ship 0.0.6 now"',
    digest: "sha256:lin2",
  });
  await expect(card).toContainText("updated");
  await expect(page.getByTestId("approval-card")).toHaveCount(1);
});

test("Stop while a card waits sends stop, never an answer, and frees the composer", async ({ page, host }) => {
  await waitOnLinearApproval(page, host);
  await page.getByTestId(ids.stop).click();
  await expect.poll(() => host.sentOfType("stop").length).toBe(1);
  expect(host.sentOfType("approve")).toHaveLength(0);
  await expect(page.getByTestId("approval-card")).toHaveCount(0);
  await expect(page.getByTestId(ids.input)).toBeEnabled();
});

test("host errors on approve show a toast, clear the card, and never retry", async ({ page, host }) => {
  await openChat(page);
  await send(page, "run");
  host.toolCall({ call_id: "c1", name: "run_command" });
  host.emitApproval({ approval_id: "ap_err", call_id: "c1", summary: "Run x", digest: "sha256:e1" });
  host.approveError = { code: "unknown_approval", message: "Approval 'ap_err' is unknown or has already finished." };

  await page.getByTestId("approval-card").getByRole("button", { name: "Allow", exact: true }).click();
  await expect(page.locator("[data-sonner-toast]")).toContainText("already finished");
  await expect(page.getByTestId("approval-card")).toHaveCount(0);
  await page.waitForTimeout(300);
  expect(host.sentOfType("approve")).toHaveLength(1);
});

test("reconnect within grace keeps the card answerable; grace expiry and hard refresh clear cleanly @cross", async ({
  page,
  host,
}) => {
  await openChat(page);
  await send(page, "deploy");
  host.toolCall({ call_id: "c1", name: "run_command" });
  host.emitApproval({ approval_id: "ap_grace", call_id: "c1", summary: "Run deploy", digest: "sha256:gr1" });
  await expect(page.getByTestId("approval-card")).toBeVisible();

  host.statusReply = { message: "", turn_streamed_chunks: 0, active_tool: "run_command", partial_text: null };
  await host.drop();
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "open", { timeout: 10_000 });

  const card = page.getByTestId("approval-card");
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Allow", exact: true }).click();
  await expect.poll(() => host.sentOfType("approve").at(-1)).toMatchObject({
    approval_id: "ap_grace",
    answer: "yes",
    digest: "sha256:gr1",
  });
  host.done();

  // Grace expiry: the host ends the turn after the reconnect, with no approve frame.
  await send(page, "second");
  host.toolCall({ call_id: "c2", name: "run_command" });
  host.emitApproval({ approval_id: "ap_exp", call_id: "c2", summary: "Run exp", digest: "sha256:exp" });
  await expect(page.getByTestId("approval-card")).toBeVisible();
  await host.drop();
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "open", { timeout: 10_000 });
  const approvesBefore = host.sentOfType("approve").length;
  host.done();
  await expect(page.getByTestId("approval-card")).toHaveCount(0);
  expect(host.sentOfType("approve")).toHaveLength(approvesBefore);

  // Hard refresh loses the cached approval while Stop still works.
  await send(page, "third");
  host.toolCall({ call_id: "c3", name: "run_command" });
  host.emitApproval({ approval_id: "ap_ref", call_id: "c3", summary: "Run ref", digest: "sha256:ref" });
  await expect(page.getByTestId("approval-card")).toBeVisible();
  await page.reload();
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "open");
  await expect(page.getByTestId("approval-card")).toHaveCount(0);
  await page.getByTestId(ids.stop).click();
  await expect.poll(() => host.sentOfType("stop").length).toBeGreaterThanOrEqual(1);
});

test("take over while waiting keeps the card pending, clean density shows outside chip, and keyboard order holds", async ({
  page,
  host,
}) => {
  expect(existsSync(join(__dirname, "..", "src", "data", "chat", "approval-notice.json"))).toBe(false);
  await openChat(page);
  await send(page, "clean check");
  host.toolCall({ call_id: "c1", name: "edit_file" });
  host.emitApproval({
    approval_id: "ap_clean",
    call_id: "c1",
    tool: "edit_file",
    tier: "diff",
    summary: "Edit a.md",
    grantable: true,
    digest: "sha256:cl",
  });

  await expect(page.locator("[data-testid=approval-notice]")).toHaveCount(0);
  const card = page.getByTestId("approval-card");
  await expect(card).toBeVisible();

  // Tab order: Allow, then Always allow in this chat, then Deny.
  await card.getByRole("button", { name: "Allow", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(card.getByRole("button", { name: "Always allow in this chat" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(card.getByRole("button", { name: "Deny" })).toBeFocused();

  // A take-over while the card waits: the dialog shows first, then the card is still pending.
  host.statusReply = { message: "", turn_streamed_chunks: 0, active_tool: "edit_file", partial_text: null };
  await host.drop(4000);
  await expect(page.getByTestId("take-over")).toBeVisible();
  await page.getByTestId("take-over").getByRole("button", { name: "Take over" }).click();
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "open", { timeout: 10_000 });
  await expect(card.getByRole("button", { name: "Allow", exact: true })).toBeVisible();

  await card.getByRole("button", { name: "Deny" }).focus();
  await page.keyboard.press("Enter");
  await expect(card.getByTestId("approval-deny-reason")).toBeFocused();
  await page.keyboard.type("keyboard reason");
  await page.keyboard.press("Enter");
  await expect.poll(() => host.sentOfType("approve").at(-1)).toMatchObject({
    approval_id: "ap_clean",
    answer: "no",
    reason: "keyboard reason",
  });
});

test("in clean density a settled card leaves with the turn; in detail it stays on its row", async ({ page, host }) => {
  const approval = {
    approval_id: "ap_w",
    call_id: "w1",
    tool: "write_file",
    tier: "diff" as const,
    summary: "Create notes.md (new file, 3 lines)",
    grantable: true,
    digest: "sha256:w1",
    display: {
      kind: "diff" as const,
      path: "notes.md",
      additions: 1,
      deletions: 0,
      truncated: false,
      lines: [{ kind: "added" as const, text: "apples" }],
    },
  };
  await openChat(page);
  await send(page, "make notes");
  host.toolCall({ call_id: "w1", name: "write_file" });
  host.emitApproval(approval);
  const card = page.getByTestId("approval-card");
  await card.getByRole("button", { name: "Allow", exact: true }).click();
  host.emitToolResult({ call_id: "w1", ok: true, summary: "Write notes.md", display: approval.display });
  await expect(card).toBeVisible();
  await host.stream("I created notes.md.");
  host.done({ reason: "answered" });
  await expect(card).toHaveCount(0);
  await expect(page.getByTestId("assistant-message")).toContainText("I created notes.md.");

  await setDensity(page, "detail");
  await expect(card).toBeVisible();
});

test("the pill goes to the waiting card, not an earlier answered one", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "two commands");
  host.toolCall({ call_id: "c1", name: "run_command" });
  host.emitApproval({ approval_id: "ap_first", call_id: "c1", summary: "Run date", digest: "sha256:f1" });
  await page.locator('[data-approval-id="ap_first"]').getByRole("button", { name: "Allow", exact: true }).click();
  host.emitToolResult({ call_id: "c1", ok: true, summary: "exit 0" });
  host.toolCall({ call_id: "c2", name: "run_command" });
  host.emitApproval({ approval_id: "ap_second", call_id: "c2", summary: "Run uptime", digest: "sha256:f2" });

  const waiting = page.locator('[data-approval-id="ap_second"]');
  await expect(page.getByTestId("approval-card")).toHaveCount(2);
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  await page.getByTestId("approval-pill").click();
  await expect(waiting.getByRole("button", { name: "Allow", exact: true })).toBeFocused();
  await expect(waiting).toBeInViewport();
});
