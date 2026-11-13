import type { Page } from "@playwright/test";

import { expect, openChat, test } from "./fixtures/test";

const input = (page: Page) => page.getByTestId("composer-input");
const skillsMenu = (page: Page) => page.getByTestId("skills-button-menu");
const appsMenu = (page: Page) => page.getByTestId("apps-button-menu");

// Keys only reach the menu once its search box has focus, and the message only once focus is back.
async function openMenu(page: Page, button: "composer-skills" | "composer-apps-button") {
  await page.getByTestId(button).click();
  const menu = button === "composer-skills" ? skillsMenu(page) : appsMenu(page);
  await expect(menu.getByRole("combobox")).toBeFocused();
  await expect(menu.getByRole("option").first()).toBeVisible();
}

test.describe("the @ and apps buttons write nothing until something is picked @cross", () => {
  test("opening the skills menu leaves the message untouched", async ({ page }) => {
    await openChat(page);
    await input(page).fill("hello");
    await openMenu(page, "composer-skills");
    await expect(input(page)).toHaveValue("hello");
  });

  test("closing without a pick leaves the message as it was and returns to it", async ({ page }) => {
    await openChat(page);
    await input(page).fill("hello");
    await openMenu(page, "composer-skills");
    await page.keyboard.type("fi");
    await page.keyboard.press("Escape");
    await expect(skillsMenu(page)).toBeHidden();
    await expect(input(page)).toHaveValue("hello");
    await expect(input(page)).toBeFocused();
  });

  test("a click elsewhere closes the menu and changes nothing", async ({ page }) => {
    await openChat(page);
    await page.getByTestId("composer-skills").click();
    await expect(skillsMenu(page)).toBeVisible();
    // A spot near the top: on phones the menu covers the greeting.
    const width = page.viewportSize()?.width ?? 1280;
    await page.mouse.click(width / 2, 120);
    await expect(skillsMenu(page)).toBeHidden();
    await expect(input(page)).toHaveValue("");
  });

  test("searching then Enter writes the skill into the message", async ({ page }) => {
    await openChat(page);
    await openMenu(page, "composer-skills");
    await page.keyboard.type("fi");
    await expect(skillsMenu(page).getByRole("option")).toHaveText([/^files/]);
    await page.keyboard.press("Enter");
    await expect(skillsMenu(page)).toBeHidden();
    await expect(input(page)).toHaveValue("@files ");
    await expect(input(page)).toBeFocused();
  });

  test("a clicked skill goes at the caret, with a space before it after a word", async ({ page }) => {
    await openChat(page);
    await input(page).fill("hello");
    await openMenu(page, "composer-skills");
    await skillsMenu(page).getByRole("option", { name: /^memory/ }).click();
    await expect(input(page)).toHaveValue("hello @memory ");
    await expect(skillsMenu(page)).toBeHidden();
    await expect(input(page)).toBeFocused();
    await page.keyboard.type("now");
    await expect(input(page)).toHaveValue("hello @memory now");
  });

  test("arrow keys move through the list", async ({ page }) => {
    await openChat(page);
    await openMenu(page, "composer-skills");
    await expect(skillsMenu(page).getByRole("option")).toHaveCount(4);
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(input(page)).toHaveValue("@files ");
  });

  test("an @ typed on the keyboard still opens the inline menu", async ({ page }) => {
    await openChat(page);
    await input(page).click();
    await page.keyboard.type("@");
    await expect(page.getByTestId("skills-menu").getByRole("listbox")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(input(page)).toHaveValue("@");
  });

  test("opening the apps menu leaves the message untouched, and Esc adds no chip", async ({ page }) => {
    await openChat(page);
    await input(page).fill("hello");
    await openMenu(page, "composer-apps-button");
    await expect(input(page)).toHaveValue("hello");
    await page.keyboard.press("Escape");
    await expect(input(page)).toHaveValue("hello");
    await expect(page.getByTestId("composer-apps")).toHaveCount(0);
  });

  test("picking an app adds its chip and leaves the text clean", async ({ page }) => {
    await openChat(page);
    await input(page).fill("hello");
    await openMenu(page, "composer-apps-button");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("composer-apps").locator("[data-picked-app]")).toHaveCount(1);
    await expect(input(page)).toHaveValue("hello");
  });
});
