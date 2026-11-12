import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { gzipSync } from "node:zlib";

import { expect, openChat, send, test } from "./fixtures/test";

const OUT = join(__dirname, "..", "out");
// Per page, not the whole folder: no page loads every chunk.
const PAGE_JS_BUDGET = 560 * 1024;

function htmlFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "_next" ? [] : htmlFiles(path);
    return entry.name.endsWith(".html") ? [path] : [];
  });
}

test("no page loads more than 560 KiB of gzipped JS", ({ browserName }) => {
  test.skip(browserName !== "chromium", "reads the export from disk, so one browser is enough");
  const over = htmlFiles(OUT).flatMap((page) => {
    const scripts = new Set(readFileSync(page, "utf8").match(/\/_next\/static\/chunks\/[^"'\\\s]+?\.js/g) ?? []);
    const bytes = [...scripts].reduce((sum, src) => sum + gzipSync(readFileSync(join(OUT, src)), { level: 9 }).length, 0);
    return bytes > PAGE_JS_BUDGET ? [`${relative(OUT, page)}: ${Math.round(bytes / 1024)} KiB`] : [];
  });
  expect(over).toEqual([]);
});

test("fifty deltas a second has no long tasks @timing", async ({ page, host, browserName }) => {
  test.skip(browserName !== "chromium", "long task timing is Chromium-only");
  await page.addInitScript(() => {
    const w = window as unknown as { __long: number[] };
    w.__long = [];
    new PerformanceObserver((list) => list.getEntries().forEach((e) => w.__long.push(e.duration))).observe({ type: "longtask", buffered: true });
  });
  await openChat(page);
  await send(page, "stream");
  await page.evaluate(() => ((window as unknown as { __long: number[] }).__long = []));
  await host.stream("lorem ipsum dolor sit amet, ".repeat(70), { chunkSize: 4, delayMs: 20 });
  host.done();
  const long = await page.evaluate(() => (window as unknown as { __long: number[] }).__long);
  expect(long.filter((d) => d > 50)).toEqual([]);
});

test("chat paints within a second @timing", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "paint timing is Chromium-only");
  const times: number[] = [];
  for (let i = 0; i < 3; i += 1) {
    await page.goto("/");
    await expect(page.getByTestId("aldwin-chat-shell")).toBeVisible();
    // The entry is recorded just after the frame paints, so wait for it rather than read it once.
    await expect.poll(() => page.evaluate(() => performance.getEntriesByName("first-contentful-paint").length)).toBe(1);
    times.push(await page.evaluate(() => performance.getEntriesByName("first-contentful-paint")[0].startTime));
  }
  times.sort((a, b) => a - b);
  expect(times[1]).toBeLessThan(1000);
});
