import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, openChat, send, test } from "./fixtures/test";

const HOSTILE = readFileSync(join(__dirname, "fixtures", "hostile.md"), "utf8");

test("tables and code render with copy", async ({ page, host }) => {
  host.answerNext("| a | b |\n| - | - |\n| 1 | 2 |\n\n```ts\nconst a = 1;\n```");
  await openChat(page);
  await send(page, "q");
  const answer = page.getByTestId("assistant-message");
  await expect(answer.locator("table td")).toHaveCount(2);
  await expect(answer.locator("pre code")).toHaveText("const a = 1;");
});

test("script, frames, styles and script urls never run", async ({ page, host }) => {
  const dialogs: string[] = [];
  page.on("dialog", (dialog) => {
    dialogs.push(dialog.message());
    void dialog.dismiss();
  });
  host.answerNext(HOSTILE);
  await openChat(page);
  await send(page, "q");
  const answer = page.getByTestId("assistant-message");
  await expect(answer).toContainText("bold");
  await expect(answer.locator("script, iframe, style, img[src=x], b")).toHaveCount(0);
  await expect(answer.locator('a[href^="javascript:"]')).toHaveCount(0);
  await page.waitForTimeout(300);
  expect(dialogs).toEqual([]);
});

test("an image becomes a link and is never fetched", async ({ page, host }) => {
  const outside: string[] = [];
  page.on("request", (request) => {
    if (!request.url().startsWith("http://127.0.0.1")) outside.push(request.url());
  });
  host.answerNext("![chart](https://example.com/pixel.png?d=secret)");
  await openChat(page);
  await send(page, "q");
  const answer = page.getByTestId("assistant-message");
  const link = answer.getByRole("link", { name: "chart" });
  await expect(link).toHaveAttribute("href", "https://example.com/pixel.png?d=secret");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await expect(answer.locator("img")).toHaveCount(0);
  await page.waitForTimeout(300);
  expect(outside).toEqual([]);
});

test("links open in a new tab with noopener", async ({ page, host }) => {
  host.answerNext("[docs](https://example.com)");
  await openChat(page);
  await send(page, "q");
  const link = page.getByTestId("assistant-message").getByRole("link", { name: "docs" });
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
});
