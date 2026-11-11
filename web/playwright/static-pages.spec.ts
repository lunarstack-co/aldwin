import { expect, test } from "@playwright/test";

test("the dev kit is not in the export", async ({ request }) => {
  const response = await request.get("/dev/kit/");
  expect(response.status()).toBe(404);
  expect(await response.text()).toContain("Page not found");
});
