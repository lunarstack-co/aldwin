import { test as base, expect, type Page, type TestInfo } from "@playwright/test";

import { FakeHost } from "./fake-host";

export { expect };
export * from "./fake-host";

/** `host` plays Aldwin for the page; nothing else answers `/v1`. */
export const test = base.extend<{ host: FakeHost }>({
  host: [
    async ({ page }, provide) => {
      const host = new FakeHost(page);
      await host.install();
      await provide(host);
    },
    { auto: true },
  ],
});

export const ids = {
  shell: "aldwin-chat-shell",
  status: "connection-status",
  input: "composer-input",
  send: "composer-send",
  stop: "composer-stop",
  turn: "turn",
} as const;

/**
 * Store the project's colour scheme as the chosen theme before the page loads.
 * Aldwin opens dark whatever the browser prefers, so light projects need this
 * to draw light.
 */
export async function useProjectTheme(page: Page, testInfo: TestInfo): Promise<void> {
  const scheme = testInfo.project.use.colorScheme === "light" ? "light" : "dark";
  await page.addInitScript((theme) => {
    try {
      if (!localStorage.getItem("theme")) localStorage.setItem("theme", theme);
    } catch {
      // Storage may be blocked; the default theme is then fine.
    }
  }, scheme);
}

/** Open the chat with the gates clear and wait for the socket to settle. */
export async function openChat(page: Page, path = "/"): Promise<void> {
  await page.goto(path);
  await expect(page.getByTestId(ids.status)).toHaveAttribute("data-phase", "open");
}

export async function send(page: Page, text: string): Promise<void> {
  await page.getByTestId(ids.input).fill(text);
  await page.getByTestId(ids.input).press("Enter");
}

/** Pick a density from the view menu. */
export async function setDensity(page: Page, density: "clean" | "detail"): Promise<void> {
  await page.getByTestId("density-toggle").click();
  await page.getByTestId(`density-${density}`).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
}

/** Pick a theme from the view menu. */
export async function setTheme(page: Page, theme: "light" | "dark" | "system"): Promise<void> {
  await page.getByTestId("density-toggle").click();
  await page.getByTestId(`theme-${theme}`).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).toHaveCount(0);
}
