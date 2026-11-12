import { expect, openChat, send, setDensity, test } from "./fixtures/test";

test("a finished tool shows its details", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "q");
  host.toolCall({ call_id: "c1", name: "read_file", args_preview: '{"path":"a.md"}', summary: "Reading a.md" });
  host.toolResult({ call_id: "c1", ok: true, summary: "12 lines", preview: "# Title", duration_ms: 840 });
  const card = page.locator('[data-testid=tool-card][data-state=ok]');
  await expect(card).toContainText("read_file");
  await expect(card).toContainText("840 ms");
  await card.getByRole("button").first().click();
  await expect(card).toContainText('{"path":"a.md"}');
  await expect(card).toContainText("12 lines");
  await expect(card.locator("pre code").filter({ hasText: "# Title" })).toBeVisible();
});

test("connector calls show their source", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "q");
  host.toolCall({ call_id: "c1", connector: "github", connector_title: "GitHub", remote_tool: "search_issues", reason: "Find open bugs" });
  host.toolCall({ call_id: "c2", name: "plain" });
  const cards = page.getByTestId("tool-card");
  await expect(cards.nth(0).locator("[data-slot=badge]")).toHaveText("GitHub");
  await expect(cards.nth(0)).toContainText("search_issues");
  await expect(cards.nth(1).locator("[data-slot=badge]")).toHaveCount(0);
  host.toolResult({ call_id: "c1", ok: true });
  await cards.nth(0).getByRole("button").first().click();
  await expect(cards.nth(0)).toContainText("Find open bugs");
});

test("a failed tool is marked", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "q");
  host.toolCall();
  host.toolResult({ ok: false, summary: "Permission denied" });
  const card = page.locator('[data-testid=tool-card][data-state=failed]');
  await expect(card).toBeVisible();
  const colours = await card.evaluate((el) => {
    const icon = el.querySelector("svg")!;
    const probe = document.createElement("span");
    probe.style.color = "var(--destructive)";
    el.append(probe);
    const want = getComputedStyle(probe).color;
    probe.remove();
    return { icon: getComputedStyle(icon).color, want };
  });
  expect(colours.icon).toBe(colours.want);
});

test("a running tool shimmers", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "q");
  host.toolCall();
  await expect(page.locator('[data-testid=tool-card][data-state=running] .shimmer')).toBeVisible();
});

test("consecutive tools group open in detail", async ({ page, host }) => {
  host.answerNext("Done.", [{ name: "a", ok: true }, { name: "b", ok: true }, { name: "c", ok: true }]);
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "q");
  const group = page.getByTestId("tool-group");
  await expect(group).toHaveCount(1);
  await expect(group).toHaveAttribute("data-open", "");
  await expect(group.getByTestId("tool-card")).toHaveCount(3);
});

test("density defaults to clean and is remembered", async ({ page, host }) => {
  host.answerNext("Done.", [{ name: "a", ok: true }]);
  await openChat(page);
  expect(await page.evaluate(() => localStorage.getItem("aldwin.density"))).toBeNull();
  await setDensity(page, "detail");
  await page.reload();
  expect(await page.evaluate(() => localStorage.getItem("aldwin.density"))).toBe("detail");
});
