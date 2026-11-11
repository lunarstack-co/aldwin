import { expect, openChat, setTheme, test } from "./fixtures/test";

test("opens in dark with no stored choice", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await openChat(page);
  await expect(page.locator("html")).toHaveClass(/dark/);
});

test("light survives a reload without a flash", async ({ page }) => {
  await openChat(page);
  await setTheme(page, "light");
  await page.addInitScript(() => {
    document.addEventListener("DOMContentLoaded", () => {
      (window as unknown as { __firstClass: string }).__firstClass = document.documentElement.className;
    });
  });
  await page.reload();
  const first = await page.evaluate(() => (window as unknown as { __firstClass: string }).__firstClass);
  expect(first).toContain("light");
  expect(first).not.toContain("dark");
  expect(await page.evaluate(() => localStorage.getItem("theme"))).toBe("light");
});

test("system follows prefers-color-scheme", async ({ page }) => {
  await openChat(page);
  await setTheme(page, "system");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveClass(/light/);
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveClass(/dark/);
});

test("switching theme sends nothing to the host", async ({ page, host }) => {
  await openChat(page);
  const before = host.sent.length;
  await setTheme(page, "light");
  await setTheme(page, "dark");
  await setTheme(page, "system");
  expect(host.calls.filter((c) => c.path.includes("/settings") && c.method !== "GET")).toEqual([]);
  expect(host.sent.slice(before).some((f) => JSON.stringify(f).includes("theme"))).toBe(false);
});
