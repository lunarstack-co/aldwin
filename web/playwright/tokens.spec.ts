import { expect, openChat, setTheme, test } from "./fixtures/test";

async function token(page: import("@playwright/test").Page, name: string) {
  return page.evaluate((n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(), name);
}

test("light tokens equal the assistant-ui source", async ({ page }) => {
  await openChat(page);
  await setTheme(page, "light");
  expect(await token(page, "--tint")).toBe("106");
  expect(await token(page, "--background")).toMatch(/^oklch\(0?\.992 0?\.002 (106|var\(--tint\))\)$/);
  expect(await token(page, "--foreground")).toMatch(/^oklch\(0?\.145 0?\.006 (106|var\(--tint\))\)$/);
  expect(await token(page, "--primary")).toMatch(/^oklch\(0?\.205 0?\.006 (106|var\(--tint\))\)$/);
  expect(await token(page, "--muted-foreground")).toMatch(/^oklch\(0?\.556 0?\.007 (106|var\(--tint\))\)$/);
  expect(await token(page, "--border")).toMatch(/^oklch\(0?\.922 0?\.005 (106|var\(--tint\))\)$/);
});

test("dark tokens equal the assistant-ui source", async ({ page }) => {
  await openChat(page);
  await setTheme(page, "dark");
  expect(await token(page, "--background")).toMatch(/^oklch\(0?\.17 0?\.003 (106|var\(--tint\))\)$/);
  expect(await token(page, "--foreground")).toMatch(/^oklch\(0?\.985 0?\.002 (106|var\(--tint\))\)$/);
  expect(await token(page, "--border")).toMatch(/^oklch\(0?\.985 0?\.002 (106|var\(--tint\)) \/ (10%|0?\.1)\)$/);
  expect(await token(page, "--destructive")).toMatch(/^(oklch\(0?\.704 0?\.191 22\.216\)|lab\(63\.70\d*% 60\.74\d* 31\.31\d*\))$/);
});

test("controls, surfaces and the thread use the right radius", async ({ page, host }) => {
  host.answerNext("Hi.");
  await openChat(page);
  await page.getByTestId("composer-input").fill("hello");
  await expect(page.getByTestId("composer-send")).toHaveCSS("border-radius", /9999px|3.35544e\+07px/);
  await expect(page.getByTestId("composer")).toHaveCSS("border-radius", "16px");
  await page.getByTestId("composer-input").press("Enter");
  await expect(page.getByTestId("user-bubble")).toHaveCSS("border-radius", "16px");
  await page.getByTestId("density-toggle").click();
  await expect(page.locator("[data-slot=dropdown-menu-content]")).toHaveCSS("border-radius", "10px");
});

test("only floating surfaces cast a shadow", async ({ page }) => {
  await openChat(page);
  await expect(page.getByTestId("composer")).toHaveCSS("box-shadow", "none");
  await expect(page.getByTestId("rail")).toHaveCSS("box-shadow", "none");
  await page.getByTestId("density-toggle").click();
  await expect(page.locator("[data-slot=dropdown-menu-content]")).not.toHaveCSS("box-shadow", "none");
});

test("fonts are bundled and applied", async ({ page }) => {
  const fonts: string[] = [];
  page.on("request", (request) => {
    if (request.resourceType() === "font") fonts.push(request.url());
  });
  await openChat(page);
  await expect(page.locator("body")).toHaveCSS("font-family", /public ?sans/i);
  const mono = await page.evaluate(() => {
    const code = document.createElement("code");
    document.body.append(code);
    const family = getComputedStyle(code).fontFamily;
    code.remove();
    return family;
  });
  expect(mono).toMatch(/jet ?brains ?mono/i);
  expect(fonts.length).toBeGreaterThan(0);
  for (const url of fonts) expect(url).toMatch(/^http:\/\/127\.0\.0\.1:4173\/_next\/static\/media\/.+\.woff2$/);
});
