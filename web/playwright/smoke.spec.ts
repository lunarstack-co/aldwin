import { expect, ids, openChat, test } from "./fixtures/test";

test("serves the Aldwin page with the design tokens @cross", async ({ page, host }) => {
  await openChat(page);
  await expect(page.getByTestId(ids.shell)).toBeVisible();
  const colours = await page.evaluate(() => {
    const body = getComputedStyle(document.body).backgroundColor;
    const probe = document.createElement("div");
    probe.style.backgroundColor = "var(--background)";
    document.body.append(probe);
    const token = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return { body, token };
  });
  expect(colours.body).toBe(colours.token);
  await expect.poll(() => host.sent.map((f) => f.type).slice(0, 4)).toEqual(["hello", "status", "history", "sessions.list"]);
});
