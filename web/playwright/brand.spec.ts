import { expect, openChat, setDensity, setTheme, test } from "./fixtures/test";

test("the mark is where it belongs", async ({ page, host }) => {
  host.answerNext("Done.");
  await openChat(page);
  const lockup = page.getByRole("link", { name: "Aldwin, new chat" }).first();
  await expect(lockup).toHaveAttribute("href", "/");
  await expect(lockup.locator("[data-brand-mark]")).toHaveCSS("width", "20px");
  await expect(page.getByTestId("empty-home").locator("[data-brand-mark]")).toHaveCSS("width", "40px");
  await page.getByTestId("composer-input").fill("q");
  await page.getByTestId("composer-input").press("Enter");
  await expect(page.getByTestId("turn").locator("[data-brand-mark]")).toHaveCount(0);
  await setDensity(page, "detail");
  await expect(page.getByTestId("turn").locator("[data-brand-mark]")).toHaveCSS("width", "20px");
});

test("onboarding shows the mark above the steps", async ({ page, host }) => {
  host.status = { ...host.status, keys_ok: false, host_ready: false };
  await page.goto("/onboarding/connect/");
  await expect(page.locator("aside [data-brand-mark]")).toHaveCSS("width", "32px");
});

for (const theme of ["light", "dark"] as const) {
  test(`mark uses the text colour and eyes cut to the page in ${theme}`, async ({ page }) => {
    await openChat(page);
    await setTheme(page, theme);
    const colours = await page.getByTestId("empty-home").locator("[data-brand-mark]").evaluate((svg) => {
      const probe = document.createElement("span");
      probe.style.color = "var(--background)";
      document.body.append(probe);
      const background = getComputedStyle(probe).color;
      probe.style.color = "var(--live)";
      const live = getComputedStyle(probe).color;
      probe.remove();
      return {
        fill: getComputedStyle(svg.querySelector("path")!).fill,
        color: getComputedStyle(svg).color,
        eye: getComputedStyle(svg.querySelector("rect")!).fill,
        background,
        live,
      };
    });
    expect(colours.fill).toBe(colours.color);
    expect(colours.fill).not.toBe(colours.live);
    expect(colours.eye).toBe(colours.background);
  });
}

test("decorative marks are hidden from assistive tech", async ({ page }) => {
  await openChat(page);
  const marks = page.locator("[data-brand-mark]");
  const count = await marks.count();
  for (let i = 0; i < count; i += 1) await expect(marks.nth(i)).toHaveAttribute("aria-hidden", "true");
});
