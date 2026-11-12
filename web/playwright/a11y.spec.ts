import AxeBuilder from "@axe-core/playwright";

import { expect, openChat, send, session, setDensity, test, useProjectTheme } from "./fixtures/test";

test.beforeEach(async ({ page }, testInfo) => {
  await useProjectTheme(page, testInfo);
});

const TAGS = ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"];

async function axe(page: import("@playwright/test").Page, name: string) {
  // Anything mid-fade reads as low contrast; wait for the page fade-in (which starts a frame late) and other finite motion.
  await page.waitForFunction(() => {
    const fade = document.querySelector("[data-testid=page-fade]");
    if (fade && getComputedStyle(fade).opacity !== "1") return false;
    return document.getAnimations().every((a) => a.effect?.getComputedTiming().iterations === Infinity || a.playState !== "running");
  });
  const result = await new AxeBuilder({ page }).withTags(TAGS).exclude("[data-sonner-toaster]").analyze();
  const summary = result.violations.map((v) => `${name}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target.join(" ")}`);
  expect(summary).toEqual([]);
}

test("no axe violations on the chat states", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha", "2026-11-11T09:00:00Z", true)];
  await openChat(page);
  await axe(page, "empty home");
  await send(page, "q");
  host.toolCall();
  await axe(page, "running");
  host.toolResult({ ok: false, summary: "no" });
  host.todo([{ text: "One", status: "in_progress" }, { text: "Two", status: "pending" }]);
  await host.stream("Some **text**");
  host.done({ reason: "max_steps", max_steps: 3 });
  await axe(page, "max steps with plan");
  await setDensity(page, "detail");
  await axe(page, "detail");
  await send(page, "again");
  host.error("x", "It failed");
  await axe(page, "error");
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await axe(page, "see all");
});

test("no axe violations on onboarding, take over and not found", async ({ page, host }) => {
  host.status = { ...host.status, keys_ok: false, host_ready: false, profile_done: false };
  await page.goto("/onboarding/connect/");
  await expect(page.getByRole("button", { name: "Connect", exact: true })).toBeVisible();
  await axe(page, "connect");
  host.status = { ...host.status, keys_ok: true, host_ready: true };
  await page.goto("/onboarding/personality/");
  await expect(page.getByRole("group")).toBeVisible();
  await axe(page, "personality");
  host.status = { ...host.status, profile_done: true };
  await page.goto("/nope/");
  await axe(page, "not found");
  host.refuseWith = { code: 4409, reason: "already_attached" };
  await page.goto("/");
  await expect(page.getByTestId("take-over")).toBeVisible();
  await axe(page, "take over");
});

test("the open skills menu passes axe and wires the listbox to the composer", async ({ page }) => {
  await openChat(page);
  await page.getByTestId("composer-input").click();
  await page.keyboard.type("@");
  const menu = page.getByRole("listbox", { name: "Skills" });
  await expect(menu.getByRole("option")).toHaveCount(4);
  const input = page.getByTestId("composer-input");
  await expect(input).toHaveAttribute("aria-expanded", "true");
  await expect(input).toHaveAttribute("aria-controls", (await menu.getAttribute("id"))!);
  await expect(menu.getByRole("option").first()).toHaveAttribute("aria-selected", "true");
  await axe(page, "skills menu");
});

test("a skill can be picked and sent without a pointer", async ({ page, host }) => {
  await openChat(page);
  await expect(page.getByTestId("composer-input")).toBeFocused();
  await page.keyboard.type("@");
  await expect(page.getByRole("listbox", { name: "Skills" })).toBeVisible();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await page.keyboard.type("tidy my notes");
  await page.keyboard.press("Enter");
  await expect.poll(() => host.sentOfType("turn").at(-1)?.skills).toEqual(["files"]);
});

test("the whole flow works without a pointer", async ({ page, host }) => {
  host.status = { ...host.status, profile_done: false };
  await page.goto("/onboarding/name/");
  const skip = page.getByRole("button", { name: "Skip for now" });
  for (let i = 0; i < 12 && !(await skip.evaluate((el) => el === document.activeElement)); i += 1) await page.keyboard.press("Tab");
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("composer-input")).toBeFocused({ timeout: 10_000 });
  await page.keyboard.type("hello");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("composer-stop")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("composer-send")).toBeVisible();
  await page.getByTestId("density-toggle").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menu")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toBeHidden();
  const ring = await page.getByTestId("density-toggle").evaluate((el) => {
    const style = getComputedStyle(el);
    return style.outlineStyle !== "none" || style.boxShadow !== "none";
  });
  expect(ring).toBe(true);
});

test("icon buttons are labelled", async ({ page, host }) => {
  host.answerNext("Hi.");
  await openChat(page);
  await send(page, "q");
  await expect(page.getByTestId("message-actions")).toBeVisible();
  const unnamed = await page.evaluate(() =>
    [...document.querySelectorAll("button")]
      .filter((b) => !b.textContent?.trim() && !b.getAttribute("aria-label") && !b.getAttribute("aria-labelledby") && !b.title)
      .map((b) => b.outerHTML.slice(0, 120)),
  );
  expect(unnamed).toEqual([]);
});

test("every Settings section passes axe", async ({ page }) => {
  for (const section of ["providers", "search", "skills", "tools", "advanced", "appearance"]) {
    await openChat(page, `/settings/?section=${section}`);
    await expect(page.locator(`[data-testid=settings-${section}]`)).toBeVisible();
    await expect(page.locator(`[data-testid=settings-${section}]`)).not.toContainText("Loading");
    await axe(page, `settings ${section}`);
  }
});

test("the open model picker and a sign-in panel pass axe", async ({ page, host }) => {
  await openChat(page);
  await page.getByTestId("model-picker-composer").click();
  await expect(page.getByTestId("model-picker-menu").getByRole("option").first()).toBeVisible();
  await axe(page, "model picker");
  await page.keyboard.press("Escape");
  await openChat(page, "/settings/?section=tools");
  await page.locator('[data-connector="notion"]').getByRole("button", { name: "Authenticate" }).click();
  await expect.poll(() => host.sentOfType("connectors.login").length).toBe(1);
  host.connectorAuth("notion", "paste", { url: "https://notion.example.com/authorize" });
  await expect(page.getByTestId("connector-sign-in")).toBeVisible();
  await axe(page, "sign-in panel");
});

test("Settings works without a pointer", async ({ page, host }) => {
  await openChat(page);
  const settings = page.getByTestId("settings").filter({ visible: true });
  for (let i = 0; i < 20 && !(await settings.evaluate((el) => el === document.activeElement)); i += 1) await page.keyboard.press("Tab");
  await expect(settings).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/settings\/$/);
  const advanced = page.getByTestId("settings-nav").getByRole("link", { name: "Advanced" });
  for (let i = 0; i < 20 && !(await advanced.evaluate((el) => el === document.activeElement)); i += 1) await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await expect(page.locator("#settings-section-title")).toHaveText("Advanced");
  const steps = page.getByTestId("limit-max_steps");
  for (let i = 0; i < 20 && !(await steps.evaluate((el) => el === document.activeElement)); i += 1) await page.keyboard.press("Tab");
  await expect(steps).toBeFocused();
  await page.keyboard.press("ControlOrMeta+a");
  await page.keyboard.type("20");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("advanced-form")).toContainText("Saved");
  expect(host.settings.limits.max_steps).toBe(20);
});

test("the Add an app dialog passes axe", async ({ page }) => {
  await openChat(page, "/settings/?section=tools");
  await page.getByTestId("add-app-open").click();
  await expect(page.getByTestId("add-app").locator("[data-catalog]").first()).toBeVisible();
  await axe(page, "add an app");
});

test("the open apps menu and a picked app chip pass axe", async ({ page }) => {
  await openChat(page);
  await page.getByTestId("composer-input").click();
  await page.keyboard.type("/");
  await expect(page.getByTestId("apps-menu").getByRole("option").first()).toBeVisible();
  await axe(page, "apps menu");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("composer-apps")).toBeVisible();
  await axe(page, "picked app");
});

test("the leave-without-saving dialog passes axe", async ({ page }) => {
  await openChat(page, "/settings/?section=advanced");
  await page.getByTestId("limit-max_steps").fill("30");
  await page.getByRole("link", { name: "Back to chat" }).click();
  await expect(page.getByTestId("leave-dialog")).toBeVisible();
  await axe(page, "leave dialog");
});

test("the remove-app dialog passes axe", async ({ page }) => {
  await openChat(page, "/settings/?section=tools");
  await page.locator('[data-connector="notion"]').getByRole("button", { name: "Remove" }).click();
  await expect(page.getByTestId("remove-connector")).toBeVisible();
  await axe(page, "remove app");
});

test("approvals, grants, every display, stopped run, context ring and JSON panel pass axe", async ({
  page,
  host,
}) => {
  host.sessions = [session("s_a11y", "Audit")];
  host.current = host.sessions[0]!;
  host.conversations.set("s_a11y", { body: { meta: { id: "s_a11y" }, turns: [] } });
  await openChat(page, "/c/?id=s_a11y");
  await setDensity(page, "detail");

  await send(page, "audit");
  host.toolCall({ call_id: "c_cmd", name: "run_command" });
  host.emitApproval({
    approval_id: "ap_1",
    call_id: "c_cmd",
    tool: "run_command",
    summary: "Run ls",
    grantable: true,
    risky: ["recursive delete"],
    display: {
      kind: "terminal",
      command: "ls -la",
      cwd: "/ws",
      stdout: "",
      stderr: "",
      exit_code: null,
      duration_ms: 0,
      truncated: false,
    },
  });
  await expect(page.getByTestId("approval-card")).toBeVisible();
  await axe(page, "approval request");

  await page.getByTestId("approval-card").getByRole("button", { name: "Allow", exact: true }).click();
  await axe(page, "approval running");

  host.emitToolResult({
    call_id: "c_cmd",
    ok: true,
    summary: "exit 0",
    duration_ms: 12,
    display: {
      kind: "terminal",
      command: "ls -la",
      cwd: "/ws",
      stdout: "ok\n",
      stderr: "",
      exit_code: 0,
      duration_ms: 12,
      truncated: false,
    },
  });
  await expect(page.getByTestId("approval-card")).toContainText("Approved");
  await axe(page, "approval done");

  host.toolCall({ call_id: "c_grant", name: "read_file" });
  host.emitApproval({
    approval_id: "ap_g",
    call_id: "c_grant",
    tool: "read_file",
    summary: "Read files in ~/Documents",
    grantable: true,
    display: { kind: "grant", scope: "folder", reach: ["~/Documents"] },
  });
  await expect(page.locator('[data-approval-id="ap_g"]')).toBeVisible();
  await axe(page, "permission grant");
  await page.locator('[data-approval-id="ap_g"]').getByRole("button", { name: "Deny" }).click();
  await axe(page, "deny reason form");
  await page.locator('[data-approval-id="ap_g"]').getByTestId("approval-deny-confirm").click();
  await expect(page.locator('[data-approval-id="ap_g"]')).toContainText("Denied");
  await axe(page, "approval denied");

  const displays = [
    {
      id: "d_diff",
      name: "edit_file",
      display: {
        kind: "diff" as const,
        path: "a.py",
        additions: 1,
        deletions: 1,
        lines: [
          { kind: "removed" as const, text: "x" },
          { kind: "added" as const, text: "y" },
        ],
        truncated: false,
      },
    },
    {
      id: "d_files",
      name: "list_dir",
      display: {
        kind: "files" as const,
        root: ".",
        entries: [{ path: "src", kind: "folder" as const }, { path: "src/a.py", kind: "file" as const }],
        truncated: false,
      },
    },
    {
      id: "d_search",
      name: "web_search",
      display: {
        kind: "search" as const,
        query: "aldwin",
        results: [{ title: "Aldwin", url: "https://askaldwin.com", snippet: "Local assistant." }],
      },
    },
    {
      id: "d_page",
      name: "web_fetch",
      display: {
        kind: "page" as const,
        url: "https://askaldwin.com",
        title: "Aldwin",
        site: "askaldwin.com",
        description: "Local assistant.",
      },
    },
    {
      id: "d_table",
      name: "read_document",
      display: {
        kind: "table" as const,
        columns: [{ key: "k", label: "Key" }],
        rows: [{ k: "val" }],
        truncated: false,
      },
    },
    {
      id: "d_mem",
      name: "remember",
      display: {
        kind: "memory" as const,
        changes: [{ text: "Fact", change: "added" as const }],
      },
    },
    {
      id: "d_den",
      name: "run_command",
      display: {
        kind: "denied" as const,
        rule: "blocked_command",
        message: "Blocked by safety rule.",
      },
    },
  ];
  for (const item of displays) {
    host.toolCall({ call_id: item.id, name: item.name });
    host.emitToolResult({ call_id: item.id, ok: true, summary: "ok", display: item.display });
  }
  host.toolCall({ call_id: "d_err", name: "read_file" });
  host.emitToolResult({ call_id: "d_err", ok: false, summary: "missing", preview: "not found", display: null });
  host.todo([{ text: "Audit item", status: "in_progress" }]);
  await host.stream("Partial stopped");
  host.done({
    reason: "stopped",
    usage: { input_tokens: 110_000, output_tokens: 40 },
    context: {
      limit: 128_000,
      used: 110_000,
      segments: [{ key: "instructions", label: "Instructions", tokens: 110_000 }],
    },
  });

  await expect(page.getByTestId("stopped-run")).toBeVisible();
  await axe(page, "displays, tool failure, todo, stopped run, context ring");

  await page.getByTestId("context-ring").click();
  await expect(page.getByTestId("context-breakdown")).toBeVisible();
  await axe(page, "context breakdown");
  await page.keyboard.press("Escape");

  await page.getByTestId("density-toggle").click();
  await page.getByTestId("conversation-json-trigger").click();
  await expect(page.getByTestId("conversation-json-body")).toBeVisible();
  await axe(page, "conversation json sheet");
});
