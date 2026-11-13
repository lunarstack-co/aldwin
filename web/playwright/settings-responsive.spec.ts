import type { Page } from "@playwright/test";

import { connectorFixture } from "./fixtures/settings-fixtures";
import { expect, openChat, test } from "./fixtures/test";

const SECTIONS = ["providers", "search", "skills", "tools", "advanced", "appearance"];
// Phones, the sheet breakpoint, and the widths where the sidebar leaves the least room.
const WIDTHS = [320, 390, 600, 768, 800, 900, 1024, 1280, 1920];

/** Text that runs past its own box or past the section's edge, as short descriptions. */
async function spills(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const section = document.querySelector<HTMLElement>("[data-testid^=settings-]:not([data-testid=settings-hub]):not([data-testid=settings-nav])");
    if (!section) return ["no section"];
    const edge = section.getBoundingClientRect().right;
    const out: string[] = [];
    const walker = document.createTreeWalker(section, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent?.trim() ?? "";
      const parent = node.parentElement;
      if (!text || !parent || parent.closest("pre, [role=listbox], [data-scrolls]")) continue;
      // Text cut with an ellipsis, or inside something that clips or scrolls, cannot spill.
      let clipped = false;
      for (let el: HTMLElement | null = parent; el && el !== section; el = el.parentElement) {
        const style = getComputedStyle(el);
        if (style.textOverflow === "ellipsis" || /auto|scroll|hidden|clip/.test(style.overflowX)) {
          clipped = true;
          break;
        }
      }
      if (clipped) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const ink = range.getBoundingClientRect();
      const box = parent.getBoundingClientRect();
      if (ink.width === 0) continue;
      if (ink.right > box.right + 1) out.push(`"${text.slice(0, 24)}" runs ${Math.round(ink.right - box.right)}px out of its box`);
      else if (ink.right > edge + 1) out.push(`"${text.slice(0, 24)}" runs ${Math.round(ink.right - edge)}px past the edge`);
    }
    return out.slice(0, 4);
  });
}

/** Cards where a button is drawn on top of the app's name or description. */
async function collisions(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const hit = (a: DOMRect, b: DOMRect) =>
      a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
    const out: string[] = [];
    for (const card of document.querySelectorAll<HTMLElement>("[data-catalog], [data-connector]")) {
      const buttons = [...card.querySelectorAll("button, a")].map((b) => b.getBoundingClientRect());
      const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.textContent?.trim() ?? "";
        if (!text || node.parentElement?.closest("button, a")) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const line of range.getClientRects()) {
          if (buttons.some((b) => hit(b, line))) {
            out.push(`${card.dataset.catalog ?? card.dataset.connector}: a button covers "${text.slice(0, 20)}"`);
            break;
          }
        }
      }
    }
    return out.slice(0, 4);
  });
}

test("every settings section fits at every width @cross", async ({ page, host }) => {
  test.setTimeout(180_000);
  host.connectors.items.push(connectorFixture("mcpemails", "Mcpemails"));
  host.connectors.catalog.push(
    { name: "atlassian", title: "Atlassian", purpose: "Jira issues and Confluence pages", url: "https://mcp.atlassian.example.com/mcp", auth: "oauth", docs_url: "https://atlassian.example.com", keywords: ["jira"], token_help_url: null, added: false, category: "work" },
    { name: "monday", title: "monday.com", purpose: "Work with boards, items and updates", url: "https://mcp.monday.example.com/mcp", auth: "oauth", docs_url: "https://monday.example.com", keywords: ["boards"], token_help_url: null, added: false, category: "work" },
    { name: "basecamp", title: "Basecamp", purpose: "Projects, to-dos and messages in Basecamp", url: "https://mcp.basecamp.example.com/mcp", auth: "oauth", docs_url: "https://basecamp.example.com", keywords: ["todo"], token_help_url: null, added: false, category: "work" },
  );
  await openChat(page, "/settings/?section=providers");
  const problems: string[] = [];
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    for (const section of SECTIONS) {
      await page.goto(`/settings/?section=${section}`);
      await expect(page.getByTestId(`settings-${section}`)).toBeVisible();
      // Lists arrive over the socket; wait for the page to stop changing.
      await page.waitForTimeout(250);
      const sideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
      if (sideways) problems.push(`${section} @${width}: the page scrolls sideways`);
      for (const spill of await spills(page)) problems.push(`${section} @${width}: ${spill}`);
      for (const hit of await collisions(page)) problems.push(`${section} @${width}: ${hit}`);
    }
  }
  expect(problems).toEqual([]);
});
