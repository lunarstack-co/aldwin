import { expect, openChat, test } from "./fixtures/test";

test("the composer shows the engine in use and switches it @cross", async ({ page, host }) => {
  host.search.items = host.search.items.map((i) => (i.name === "brave" ? { ...i, connected: true, source: "file", detail: "key-…9999" } : i));
  await openChat(page);
  const picker = page.getByTestId("search-picker");
  await expect(picker).toHaveAccessibleName("Web search: Tavily");
  await expect(picker.locator('svg[data-brand="tavily"]')).toBeVisible();
  await picker.click();
  const menu = page.getByTestId("search-picker-menu");
  await expect(menu.getByRole("menuitemradio")).toHaveText(["Tavily", "Brave"]);
  await menu.getByRole("menuitemradio", { name: "Brave" }).click();
  await expect(picker).toHaveAccessibleName("Web search: Brave");
  expect(host.sentOfType("search.use")).toEqual([expect.objectContaining({ provider: "brave" })]);
});

test("with no engine connected the picker says so and links to Settings", async ({ page, host }) => {
  host.search.items = host.search.items.map((i) => ({ ...i, connected: false, active: false }));
  await openChat(page);
  const picker = page.getByTestId("search-picker");
  await expect(picker).toHaveAccessibleName("No web search");
  await picker.click();
  await expect(page.getByTestId("search-picker-menu")).toContainText("No search engine is connected yet.");
  await page.getByTestId("search-picker-manage").click();
  await expect(page).toHaveURL(/\/settings\/\?section=search$/);
});
