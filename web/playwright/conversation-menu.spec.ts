import type { Locator, Page } from "@playwright/test";

import { expect, openChat, session, test } from "./fixtures/test";

const phone = (page: Page) => (page.viewportSize()?.width ?? 1280) < 768;

async function openRail(page: Page) {
  if (phone(page) && !(await page.getByTestId("rail-sheet").isVisible())) await page.getByTestId("open-rail").click();
}

function row(page: Page, title: string, testId = "history-row"): Locator {
  return page.getByTestId(testId).filter({ visible: true, hasText: title });
}

async function openRowMenu(page: Page, title: string, testId = "history-row") {
  const item = row(page, title, testId);
  await item.hover();
  await item.locator("xpath=..").getByTestId("conversation-options").click();
  await expect(page.getByTestId("conversation-menu")).toBeVisible();
}

test.describe("the sidebar", () => {
  test("search sits beside the brand, New chat stands out, and the list is called Recents", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha")];
    await openChat(page);
    const rail = page.getByTestId("rail");
    const brand = await rail.getByRole("link", { name: /Aldwin/ }).boundingBox();
    const search = await rail.getByTestId("rail-search").boundingBox();
    const newChat = await rail.getByTestId("new-chat").boundingBox();
    expect(Math.abs(brand!.y + brand!.height / 2 - (search!.y + search!.height / 2))).toBeLessThan(6);
    expect(search!.x).toBeGreaterThan(brand!.x + brand!.width);
    expect(newChat!.y).toBeGreaterThan(search!.y + search!.height);
    await expect(rail.getByTestId("new-chat")).toHaveAttribute("aria-current", "page");
    await expect(rail.getByTestId("history-eyebrow")).toHaveText("Recents");
    await expect(rail.getByTestId("pinned-section")).toHaveCount(0);
  });

  test("a pinned chat moves to Pinned, and the section goes once nothing is pinned @cross", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha"), session("b", "Beta")];
    await openChat(page);
    await openRail(page);
    await openRowMenu(page, "Beta");
    await page.getByTestId("conversation-pin").click();
    await expect.poll(() => host.sentOfType("sessions.pin")).toEqual([
      expect.objectContaining({ session_id: "b", pinned: true }),
    ]);
    await expect(row(page, "Beta", "pinned-row")).toBeVisible();
    await expect(page.getByTestId("pinned-eyebrow").filter({ visible: true })).toHaveText("Pinned");
    await expect(row(page, "Beta")).toHaveCount(0);

    await openRowMenu(page, "Beta", "pinned-row");
    await expect(page.getByTestId("conversation-pin")).toHaveText("Unpin chat");
    await page.getByTestId("conversation-pin").click();
    await expect(page.getByTestId("pinned-section").filter({ visible: true })).toHaveCount(0);
    await expect(row(page, "Beta")).toBeVisible();
  });
});

test.describe("renaming", () => {
  test("the dialog starts on the current name, and Save sends the new one @cross", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha")];
    await openChat(page);
    await openRail(page);
    await openRowMenu(page, "Alpha");
    await page.getByTestId("conversation-rename").click();
    const input = page.getByTestId("rename-input");
    await expect(input).toHaveValue("Alpha");
    await expect(input).toBeFocused();
    await expect(page.getByTestId("rename-save")).toBeDisabled();
    await page.keyboard.type("Trip to Lisbon");
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("rename-dialog")).toBeHidden();
    expect(host.sentOfType("sessions.rename")).toEqual([
      expect.objectContaining({ session_id: "a", title: "Trip to Lisbon" }),
    ]);
    await openRail(page);
    await expect(row(page, "Trip to Lisbon")).toBeVisible();
  });

  test("a blank name cannot be saved and Cancel sends nothing", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha")];
    await openChat(page);
    await openRowMenu(page, "Alpha");
    await page.getByTestId("conversation-rename").click();
    await page.getByTestId("rename-input").fill("   ");
    await expect(page.getByTestId("rename-save")).toBeDisabled();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByTestId("rename-dialog")).toBeHidden();
    expect(host.sentOfType("sessions.rename")).toHaveLength(0);
  });

  test("the host's refusal shows in the dialog, which stays open", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha")];
    host.changeError = { code: "busy", message: "Another Aldwin window is answering in that conversation." };
    await openChat(page);
    await openRowMenu(page, "Alpha");
    await page.getByTestId("conversation-rename").click();
    await page.getByTestId("rename-input").fill("New name");
    await page.getByTestId("rename-save").click();
    await expect(page.getByTestId("rename-dialog").getByRole("alert")).toHaveText(
      "Another Aldwin window is answering in that conversation.",
    );
    await expect(row(page, "Alpha")).toBeVisible();
  });

  test("the header title opens the same menu for the open conversation", async ({ page, host }) => {
    host.sessions = [session("g", "Groceries")];
    host.current = host.sessions[0];
    host.history = [{ role: "user", text: "q" }, { role: "assistant", text: "a" }];
    await openChat(page, "/c/?id=g");
    await page.getByTestId("conversation-title").click();
    await page.getByTestId("conversation-rename").click();
    await page.getByTestId("rename-input").fill("Weekly shop");
    await page.getByTestId("rename-save").click();
    await expect(page.getByTestId("conversation-title")).toHaveText("Weekly shop");
  });
});

test.describe("deleting", () => {
  test("asks first, names the chat, and Cancel keeps it", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha"), session("b", "Beta")];
    await openChat(page);
    await openRowMenu(page, "Beta");
    await page.getByTestId("conversation-delete").click();
    const dialog = page.getByTestId("delete-dialog");
    await expect(dialog).toContainText("Delete chat?");
    await expect(dialog).toContainText("“Beta”");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(host.sentOfType("sessions.delete")).toHaveLength(0);
    await expect(row(page, "Beta")).toBeVisible();
  });

  test("confirming removes the chat from the list @cross", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha"), session("b", "Beta")];
    await openChat(page);
    await openRail(page);
    await openRowMenu(page, "Beta");
    await page.getByTestId("conversation-delete").click();
    await page.getByTestId("delete-confirm").click();
    await expect(page.getByTestId("delete-dialog")).toBeHidden();
    expect(host.sentOfType("sessions.delete")).toEqual([expect.objectContaining({ session_id: "b" })]);
    await openRail(page);
    await expect(row(page, "Beta")).toHaveCount(0);
    await expect(row(page, "Alpha")).toBeVisible();
  });

  test("deleting the open chat lands on a new chat", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha"), session("g", "Groceries")];
    host.current = host.sessions[1];
    host.history = [{ role: "user", text: "q" }, { role: "assistant", text: "a" }];
    await openChat(page, "/c/?id=g");
    await expect(page.getByTestId("conversation-title")).toHaveText(/Groceries/);
    const opensBefore = host.sentOfType("sessions.open").length;
    await page.getByTestId("conversation-title").click();
    await page.getByTestId("conversation-delete").click();
    await page.getByTestId("delete-confirm").click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId("empty-home")).toBeVisible();
    await expect(row(page, "Groceries")).toHaveCount(0);
    // The deleted chat is never asked for again.
    expect(host.sentOfType("sessions.open")).toHaveLength(opensBefore);
  });

  test("a refusal keeps the dialog open with the host's reason", async ({ page, host }) => {
    host.sessions = [session("a", "Alpha")];
    host.changeError = { code: "busy", message: "Aldwin is still working in this conversation. Stop it before deleting it." };
    await openChat(page);
    await openRowMenu(page, "Alpha");
    await page.getByTestId("conversation-delete").click();
    await page.getByTestId("delete-confirm").click();
    await expect(page.getByTestId("delete-dialog").getByRole("alert")).toContainText("Stop it before deleting it.");
    await expect(row(page, "Alpha")).toBeVisible();
  });
});

test("a new chat has no title menu", async ({ page }) => {
  await openChat(page);
  await expect(page.getByTestId("conversation-title")).toHaveCount(0);
});
