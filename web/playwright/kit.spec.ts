// Runs against `next dev` only (PW_KIT=1), where the gallery page exists.
import { readdirSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { CLEAR } from "./fixtures/fake-host";

test("every ui component renders in both themes", async ({ page }) => {
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
  const overlays = ["combobox", "dialog", "dropdown-menu", "select", "sheet", "sonner", "tooltip"];
  expect(files.filter((name) => !overlays.includes(name) && !shown.includes(name))).toEqual([]);
  await expect(page.getByRole("region", { name: "Light" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Dark" })).toHaveClass(/dark/);
  expect(errors).toEqual([]);
});
