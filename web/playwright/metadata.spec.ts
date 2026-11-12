import { expect, openChat, send, session, test } from "./fixtures/test";

const PAGES: [string, string][] = [
  ["/", "New chat · Aldwin"],
  ["/onboarding/connect/", "Connect a model · Aldwin"],
  ["/onboarding/name/", "Your name · Aldwin"],
  ["/onboarding/occupation/", "Your work · Aldwin"],
  ["/onboarding/personality/", "Answer style · Aldwin"],
  ["/404.html", "Page not found · Aldwin"],
];

test("each page has its own title and description", async ({ request }) => {
  const descriptions = new Set<string>();
  for (const [path, title] of PAGES) {
    const html = await (await request.get(path)).text();
    expect(html).toContain(`<title>${title}</title>`);
    const description = html.match(/<meta name="description" content="([^"]+)"/)?.[1] ?? "";
    expect(description.length).toBeGreaterThan(0);
    expect(description.length).toBeLessThanOrEqual(155);
    descriptions.add(description);
    expect(html).toContain('<meta name="robots" content="noindex, nofollow"/>');
    expect(html).toContain('name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"');
    expect(html).toMatch(/name="format-detection" content="telephone=no"/);
    expect(html.match(/name="theme-color"/g)).toHaveLength(2);
  }
  expect(descriptions.size).toBe(PAGES.length);
});

test("theme colours follow the tokens", async ({ request }) => {
  const html = await (await request.get("/")).text();
  expect(html).toContain('<meta name="theme-color" content="#fcfcfb" media="(prefers-color-scheme: light)"/>');
  expect(html).toContain('<meta name="theme-color" content="#0f0f0e" media="(prefers-color-scheme: dark)"/>');
  const manifest = await (await request.get("/manifest.webmanifest")).json();
  expect(manifest.theme_color).toBe("#0f0f0e");
  expect(manifest.background_color).toBe("#0f0f0e");
});

test("conversation title follows the session @cross", async ({ page, host }) => {
  host.sessions = [session("a", "Plan the Lisbon trip"), session("l", "x".repeat(70))];
  await openChat(page, "/c/?id=a");
  await expect(page).toHaveTitle("Plan the Lisbon trip · Aldwin");
  await page.goto("/c/?id=l");
  await expect(page).toHaveTitle(`${"x".repeat(59)}… · Aldwin`);
});

test("a new chat is named by its first message", async ({ page, host }) => {
  await openChat(page);
  await send(page, "Hello there");
  await expect(page).toHaveTitle("● Hello there · Aldwin");
  await host.stream("Hi");
  host.done();
  await expect(page).toHaveTitle("Hello there · Aldwin");
  await send(page, "Break");
  await expect(page).toHaveTitle(/^● /);
  host.error("x", "boom");
  await expect(page).toHaveTitle("Hello there · Aldwin");
});

test("descriptions never carry message text", async ({ page, host }) => {
  host.answerNext("zebra-417");
  await openChat(page);
  await send(page, "zebra-417 please");
  await expect(page.getByTestId("assistant-message")).toContainText("zebra-417");
  const description = await page.locator('meta[name="description"]').first().getAttribute("content");
  expect(description).not.toContain("zebra");
});

test("unknown paths show not found @cross", async ({ page }) => {
  const response = await page.goto("/nope/");
  expect(response?.status()).toBe(404);
  const page404 = page.getByTestId("not-found");
  await expect(page404.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await expect(page404.locator("[data-brand-mark]")).toHaveCSS("width", "40px");
  await page404.getByRole("link", { name: "Go to chat" }).click();
  await expect(page).toHaveURL(/127\.0\.0\.1:4173\/$/);
});
