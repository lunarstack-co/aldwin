import type { Page } from "@playwright/test";

import { expect, openChat, test } from "./fixtures/test";

const input = (page: Page) => page.getByTestId("composer-input");
const skillsMenu = (page: Page) => page.getByTestId("skills-menu").getByRole("listbox");
const appsMenu = (page: Page) => page.getByTestId("apps-menu").getByRole("listbox");

test.describe("the @ and apps buttons only browse until something is picked @cross", () => {
  test("@ then Esc leaves the message as it was", async ({ page }) => {
    await openChat(page);
    await page.getByTestId("composer-skills").click();
    await expect(skillsMenu(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(skillsMenu(page)).toBeHidden();
    await expect(input(page)).toHaveValue("");
  });

  test("@ then a click elsewhere leaves the message as it was", async ({ page }) => {
    await openChat(page);
    await page.getByTestId("composer-skills").click();
    await expect(skillsMenu(page)).toBeVisible();
    // A spot near the top: on phones the menu covers the greeting.
    const width = page.viewportSize()?.width ?? 1280;
    await page.mouse.click(width / 2, 120);
    await expect(skillsMenu(page)).toBeHidden();
    await expect(input(page)).toHaveValue("");
  });

  test("after some text, @ then Esc takes back the space it added too", async ({ page }) => {
    await openChat(page);
    await input(page).fill("hello");
    await page.getByTestId("composer-skills").click();
    await expect(input(page)).toHaveValue("hello @");
    await page.keyboard.press("Escape");
    await expect(input(page)).toHaveValue("hello");
  });

  test("a search typed into the menu goes too when nothing is picked", async ({ page }) => {
    await openChat(page);
    await page.getByTestId("composer-skills").click();
    await page.keyboard.type("fi");
    await page.keyboard.press("Escape");
    await expect(input(page)).toHaveValue("");
  });

  test("picking a skill keeps it", async ({ page }) => {
    await openChat(page);
    await page.getByTestId("composer-skills").click();
    await page.keyboard.type("fi");
    await page.keyboard.press("Enter");
    await expect(input(page)).toHaveValue("@files ");
  });

  test("text typed past the menu is the user's and stays", async ({ page }) => {
    await openChat(page);
    await page.getByTestId("composer-skills").click();
    await page.keyboard.type(" hello");
    await expect(skillsMenu(page)).toBeHidden();
    await expect(input(page)).toHaveValue("@ hello");
  });

  test("an @ typed on the keyboard stays when the menu closes", async ({ page }) => {
    await openChat(page);
    await input(page).click();
    await page.keyboard.type("@");
    await expect(skillsMenu(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(input(page)).toHaveValue("@");
  });

  test("the apps button then Esc leaves the message as it was", async ({ page }) => {
    await openChat(page);
    await input(page).fill("hello");
    await page.getByTestId("composer-apps-button").click();
    await expect(appsMenu(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(input(page)).toHaveValue("hello");
    await expect(page.getByTestId("composer-apps")).toHaveCount(0);
  });

  test("picking an app adds its chip and leaves the text clean", async ({ page }) => {
    await openChat(page);
    await input(page).fill("hello");
    await page.getByTestId("composer-apps-button").click();
    await expect(appsMenu(page).getByRole("option").first()).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("composer-apps").locator("[data-picked-app]")).toHaveCount(1);
    await expect(input(page)).toHaveValue("hello");
  });
});
