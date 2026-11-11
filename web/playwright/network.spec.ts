import { expect, openChat, send, session, test } from "./fixtures/test";

test("every request stays on the page origin @cross", async ({ page, host }) => {
  const urls: string[] = [];
  page.on("request", (request) => urls.push(request.url()));
  host.sessions = [session("a", "Alpha")];
  host.status = { ...host.status, profile_done: false };
  await page.goto("/onboarding/name/");
  await page.getByRole("button", { name: "Skip for now" }).click();
  await expect(page.getByTestId("aldwin-chat-shell")).toBeVisible();
  host.answerNext("Done.");
  await send(page, "q");
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await page.getByTestId("see-all").filter({ visible: true }).click();
  await page.getByTestId("see-all-row").first().click();
  await expect(page).toHaveURL(/\/c\//);
  const foreign = urls.filter((url) => !url.startsWith("http://127.0.0.1:4173/") && !url.startsWith("ws://127.0.0.1:4173/") && !url.startsWith("data:"));
  expect(foreign).toEqual([]);
  expect(urls.some((url) => /assistant-ui\.com|googleapis|gstatic|jsdelivr|unpkg|cdnjs/.test(url))).toBe(false);
});

test("the app runs when storage is blocked", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", {
      get() {
        throw new Error("blocked");
      },
    });
  });
  await openChat(page);
  await expect(page.locator("html")).toHaveClass(/dark/);
  await expect(page.getByTestId("rail")).toBeVisible();
  expect(errors.filter((e) => !e.includes("ignore if recovered"))).toEqual([]);
});
