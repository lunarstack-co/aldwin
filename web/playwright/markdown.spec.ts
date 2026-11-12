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

test("Shiki colours python while it streams, loads lazily and fetches the python grammar once", async ({
  page,
  host,
}) => {
  const chunksLoaded: string[] = [];
  page.on("request", (req) => {
    const url = req.url();
    if (url.includes("/_next/static/chunks/")) chunksLoaded.push(url);
  });

  await openChat(page);
  const initialCount = chunksLoaded.length;

  await send(page, "show python");
  await host.stream("```python\ndef greet(name):\n    return name\n", { chunkSize: 50 });

  const answer = page.getByTestId("assistant-message");
  await expect(answer.locator("pre code")).toContainText("def greet");
  // Colour arrives while the fence is still open, not only once it closes.
  await expect.poll(() => answer.locator("pre span[style]").count()).toBeGreaterThan(0);

  await host.stream("```\n\n```python\nx = 42\n```\n", { chunkSize: 50 });
  host.done();

  await expect.poll(() => answer.locator("pre span[style]").count()).toBeGreaterThan(0);

  const postChunks = chunksLoaded.slice(initialCount);
  expect(postChunks.length).toBeGreaterThan(0);
  const uniquePost = new Set(postChunks);
  // Each grammar chunk is requested once even across two python blocks.
  expect(uniquePost.size).toBe(postChunks.length);
});

test("Mermaid renders valid flowcharts as SVG and falls back to raw source on invalid syntax", async ({
  page,
  host,
}) => {
  host.answerNext("```mermaid\ngraph TD; A-->B\n```\n\n```mermaid\ngraph TD; A-->\n```");
  await openChat(page);
  await send(page, "diagram");

  const answer = page.getByTestId("assistant-message");
  await expect(answer.locator('[data-slot="mermaid-diagram"] svg')).toBeVisible();
  const fallback = answer.locator('[data-slot="mermaid-fallback"]');
  await expect(fallback).toBeVisible();
  await expect(fallback).toContainText("A-->");
});

test("KaTeX renders inline and display maths while leaving currency dollars plain @cross", async ({
  page,
  host,
}) => {
  host.answerNext("Euler: $e^{i\\pi} + 1 = 0$\n\n$$\\int_0^1 x\\,dx$$\n\nIt costs $5 and $10 today.");
  await openChat(page);
  await send(page, "maths");

  const answer = page.getByTestId("assistant-message");
  await expect(answer.locator(".katex")).toHaveCount(2);
  await expect(answer.locator(".katex-display")).toHaveCount(1);
  await expect(answer).toContainText("It costs $5 and $10 today.");
});

test("a streaming Mermaid block goes from placeholder to diagram once, never flashing source or an error", async ({
  page,
  host,
}) => {
  const chart = [
    "Here is the flow:",
    "",
    "```mermaid",
    "flowchart TD",
    "    A([Open login page]) --> B[Enter email and password]",
    "    B --> C{Fields valid?}",
    "    C -- No --> D[Show validation error] --> B",
    "    C -- Yes --> E[Send credentials]",
    "    E --> F{Password correct?}",
    "    F -- No --> G[Show error] --> B",
    "    F -- Yes --> H([Open dashboard])",
    "```",
    "",
    "That covers the main paths.",
  ].join("\n");
  await openChat(page);
  await page.evaluate(() => {
    const w = window as unknown as { mermaidStates: string[] };
    w.mermaidStates = [];
    const record = () => {
      const answer = document.querySelector('[data-testid="assistant-message"]');
      if (!answer) return;
      const state = answer.querySelector("[data-slot=mermaid-fallback]")
        ? "error"
        : answer.querySelector("[data-slot=mermaid-diagram] svg")
          ? "diagram"
          : answer.querySelector("[data-slot=mermaid-skeleton]")
            ? "placeholder"
            : [...answer.querySelectorAll("pre")].some((pre) => pre.textContent?.includes("flowchart"))
              ? "source"
              : "none";
      if (w.mermaidStates.at(-1) !== state) w.mermaidStates.push(state);
    };
    new MutationObserver(record).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  await send(page, "diagram");
  await host.stream(chart, { chunkSize: 4, delayMs: 8 });
  host.done();
  const answer = page.getByTestId("assistant-message");
  await expect(answer).toContainText("That covers the main paths.");
  await expect(answer.locator("[data-slot=mermaid-diagram] svg")).toBeVisible();
  const states = await page.evaluate(() => (window as unknown as { mermaidStates: string[] }).mermaidStates);
  expect(states.filter((state) => state !== "none")).toEqual(["placeholder", "diagram"]);
});
