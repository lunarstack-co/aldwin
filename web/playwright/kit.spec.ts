// Runs against `next dev` only (PW_KIT=1), where the gallery page exists.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { CLEAR } from "./fixtures/fake-host";

const ELEMENTS = [
  "approval-card",
  "permission-grant",
  "terminal-block",
  "code-diff",
  "file-tree",
  "web-search",
  "link-preview",
  "data-table",
  "memory-chips",
  "guardrail-notice",
  "tool-error",
  "todo-list",
  "stopped-run",
  "day-separator",
  "context-breakdown",
  "context-display",
  "message-timing",
  "shiki-highlighter",
  "mermaid-diagram",
];

test("every ui component and installed element renders in both themes", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    // The dev server's hot-reload socket is not part of the page under test.
    if (message.type() === "error" && !message.text().includes("/_next/hmr")) errors.push(message.text());
  });
  // The gallery sits under the setup gate; with no host behind `next dev`, answer it as set up.
  await page.route("**/v1/setup/status", (route) => route.fulfill({ json: CLEAR }));
  await page.goto("/dev/kit/");
  await expect(page.locator("[data-kit-item]").first()).toBeVisible();
  const shown = await page.getByRole("region", { name: "Light" }).locator("[data-kit-item]").evaluateAll((els) => els.map((el) => el.getAttribute("data-kit-item")));
  const files = readdirSync(join(__dirname, "..", "src", "components", "ui"))
    .filter((name) => name.endsWith(".tsx"))
    .map((name) => name.replace(/\.tsx$/, ""));
  const overlays = ["combobox", "dialog", "dropdown-menu", "popover", "select", "sheet", "sonner", "tooltip"];
  expect(files.filter((name) => !overlays.includes(name) && !shown.includes(name))).toEqual([]);
  expect(ELEMENTS.filter((name) => !shown.includes(name))).toEqual([]);

  const elementsDir = join(__dirname, "..", "src", "components", "assistant-ui", "elements");
  for (const name of readdirSync(elementsDir).filter((f) => f.endsWith(".ts") || f.endsWith(".tsx"))) {
    const src = readFileSync(join(elementsDir, name), "utf8");
    expect(src, name).not.toContain("@/lib/utils");
    expect(src, name).not.toContain("@/utils/cn/index");
  }
  for (const noImg of ["link-preview.tsx", "web-search.tsx"]) {
    const src = readFileSync(join(elementsDir, noImg), "utf8");
    expect(src, noImg).not.toMatch(/<img\b/);
    expect(src, noImg).not.toMatch(/\bfavicon\b/);
  }

  await expect(page.getByRole("region", { name: "Light" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Dark" })).toHaveClass(/dark/);
  expect(errors).toEqual([]);
});
