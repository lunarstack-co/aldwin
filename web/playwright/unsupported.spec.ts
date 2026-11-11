import { expect, openChat, send, setDensity, test } from "./fixtures/test";

test("no attach, dictate, edit, branch, feedback, export or suggestions", async ({ page, host }) => {
  host.answerNext("Done.", [{ name: "grep", ok: true }]);
  await openChat(page);
  const forbidden = /attach|upload|dictat|microphone|voice|edit|branch|thumb|feedback|helpful|suggest|export|share/i;
  const check = async () => {
    await expect(page.getByRole("button", { name: forbidden })).toHaveCount(0);
    await expect(page.locator('input[type="file"]')).toHaveCount(0);
  };
  await check();
  await send(page, "q");
  await expect(page.getByTestId("message-actions")).toBeVisible();
  await check();
  await setDensity(page, "detail");
  await check();
});
