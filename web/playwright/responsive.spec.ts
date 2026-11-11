import { expect, openChat, send, session, test } from "./fixtures/test";

const WIDTHS = [320, 390, 768, 1024, 1280, 1920, 2560];
const ROUTES = ["/", "/c/?id=a", "/onboarding/name/", "/404.html"];

async function noSidewaysScroll(page: import("@playwright/test").Page) {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
}

test("no horizontal scroll at any width @cross", async ({ page, host }) => {
  test.setTimeout(120_000);
  host.sessions = [session("a", "A very long conversation title that keeps going and going without stopping at all")];
  host.histories.set("a", [
    { role: "user", text: "x".repeat(300) },
    { role: "assistant", text: "```\n" + "y".repeat(200) + "\n```\n\n" + "z".repeat(300) },
  ]);
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 800 });
    for (const route of ROUTES) {
      if (route.startsWith("/onboarding")) host.status = { ...host.status, profile_done: false };
      else host.status = { ...host.status, profile_done: true };
      await page.goto(route);
      // An open socket keeps the network busy, so wait for the page itself.
      await expect(page.locator("[data-testid=aldwin-chat-shell], [data-testid=not-found], form").first()).toBeVisible();
      // A chat route may move to its conversation's address; wait until it stops moving.
      if (route === "/" || route.startsWith("/c/")) {
        await expect(page.getByTestId("connection-status")).toHaveAttribute("data-phase", "open");
        await page.waitForFunction(
          () => new Promise((resolve) => {
            const before = location.href;
            setTimeout(() => resolve(location.href === before), 300);
          }),
        );
      }
      expect(await noSidewaysScroll(page), `${route} at ${width}px`).toBe(true);
    }
  }
});

test("sidebar is a sheet on phones and a column from 768px @cross", async ({ page }) => {
  await page.setViewportSize({ width: 767, height: 800 });
  await openChat(page);
  await expect(page.getByTestId("rail")).toBeHidden();
  const open = page.getByTestId("open-rail");
  await open.click();
  const sheet = page.getByTestId("rail-sheet");
  await expect(sheet).toBeVisible();
  const box = (await sheet.boundingBox())!;
  expect(box.x).toBeLessThanOrEqual(1);
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(open).toBeFocused();
  await page.setViewportSize({ width: 768, height: 800 });
  await expect(page.getByTestId("rail")).toBeVisible();
  expect((await page.getByTestId("rail").boundingBox())!.width).toBe(260);
});

test("the sidebar slides closed and open, but not on reload", async ({ page }) => {
  await openChat(page);
  // Click in the page and record the rail's width on every frame, so the slide itself is measured.
  const slide = () =>
    page.evaluate(
      () =>
        new Promise<number[]>((resolve) => {
          const rail = document.querySelector<HTMLElement>("[data-testid=rail]")!;
          document.querySelector<HTMLElement>("[data-testid=toggle-rail]")!.click();
          const widths: number[] = [];
          const start = performance.now();
          const frame = () => {
            widths.push(Math.round(rail.getBoundingClientRect().width));
            if (performance.now() - start < 400) requestAnimationFrame(frame);
            else resolve(widths);
          };
          requestAnimationFrame(frame);
        }),
    );
  const closing = await slide();
  expect(closing.some((w) => w > 0 && w < 260)).toBe(true);
  expect(closing.at(-1)).toBe(0);
  await expect(page.getByTestId("rail")).toBeHidden();
  const opening = await slide();
  expect(opening.some((w) => w > 0 && w < 260)).toBe(true);
  expect(opening.at(-1)).toBe(260);
  await page.getByTestId("toggle-rail").click();
  await page.reload();
  await expect(page.getByTestId("rail")).toBeHidden();
  expect(await page.getByTestId("rail").evaluate((el) => getComputedStyle(el).transitionDuration)).toBe("0s");
});

test("cmd b collapses the sidebar and is remembered", async ({ page }) => {
  await openChat(page);
  await page.getByTestId("composer-input").blur();
  await page.locator("body").press("ControlOrMeta+b");
  await expect(page.getByTestId("rail")).toBeHidden();
  await page.reload();
  await expect(page.getByTestId("rail")).toBeHidden();
  await page.getByTestId("composer-input").focus();
  await page.keyboard.press("ControlOrMeta+b");
  await expect(page.getByTestId("rail")).toBeHidden();
  await page.getByTestId("toggle-rail").click();
  await expect(page.getByTestId("rail")).toBeVisible();
});

test("thread column width by breakpoint", async ({ page, host }) => {
  host.answerNext("Hi.");
  await page.setViewportSize({ width: 1024, height: 800 });
  await openChat(page);
  await send(page, "q");
  const column = page.getByTestId("transcript").locator("> div");
  expect((await column.boundingBox())!.width).toBeLessThanOrEqual(720);
  await page.setViewportSize({ width: 1440, height: 800 });
  expect((await column.boundingBox())!.width).toBeLessThanOrEqual(768);
  const stage = (await page.getByTestId("transcript").boundingBox())!;
  const box = (await column.boundingBox())!;
  expect(Math.abs(box.x - stage.x - (stage.x + stage.width - box.x - box.width))).toBeLessThanOrEqual(2);
});

test("phones get big targets and readable text @cross", async ({ page, host }) => {
  host.answerNext("Answer.");
  await page.setViewportSize({ width: 390, height: 844 });
  await openChat(page);
  await send(page, "q");
  await expect(page.getByTestId("message-actions")).toBeVisible();
  const small = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("button, a, [role=button], input, textarea")]
      .filter((el) => {
        const r = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && style.visibility !== "hidden" && !el.closest(".sr-only") && el.getAttribute("aria-hidden") !== "true";
      })
      .filter((el) => {
        const r = el.getBoundingClientRect();
        return r.height < 40 && r.width < 40 ? true : r.height < 40 && el.tagName !== "TEXTAREA" && el.tagName !== "A";
      })
      .map((el) => `${el.tagName}[${el.getAttribute("data-testid") ?? el.getAttribute("aria-label") ?? el.textContent?.trim().slice(0, 20)}] ${Math.round(el.getBoundingClientRect().width)}x${Math.round(el.getBoundingClientRect().height)}`),
  );
  expect(small).toEqual([]);
  const tiny = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("body *")]
      .filter((el) => el.childNodes.length && [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent!.trim()))
      .filter((el) => el.getBoundingClientRect().width > 0 && !el.closest(".sr-only"))
      .filter((el) => parseFloat(getComputedStyle(el).fontSize) < 12)
      .map((el) => `${el.tagName}:${el.textContent!.trim().slice(0, 20)}:${getComputedStyle(el).fontSize}`),
  );
  expect(tiny).toEqual([]);
});

test("take over is a bottom sheet on phones", async ({ page, host }) => {
  host.refuseWith = { code: 4409, reason: "already_attached" };
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const dialog = page.getByTestId("take-over");
  await expect(dialog).toBeVisible();
  // Measured once the open animation has settled; the edge ring may add a pixel.
  await expect.poll(async () => {
    const box = (await dialog.boundingBox())!;
    return Math.abs(box.y + box.height - 844) <= 2 && Math.abs(box.width - 390) <= 2;
  }).toBe(true);
});

test("composer sits on the safe area", async ({ page, host }) => {
  host.answerNext("Hi.");
  await page.setViewportSize({ width: 390, height: 844 });
  await openChat(page);
  await send(page, "q");
  const box = (await page.getByTestId("composer").boundingBox())!;
  expect(box.y + box.height).toBeLessThanOrEqual(844);
  const footer = await page.getByTestId("composer").evaluate((el) => getComputedStyle(el.parentElement!).paddingBottom);
  expect(parseFloat(footer)).toBeGreaterThanOrEqual(16);
});

test("long strings scroll in their block", async ({ page, host }) => {
  host.answerNext("```\n" + "y".repeat(400) + "\n```\n\n" + "z".repeat(300));
  await page.setViewportSize({ width: 390, height: 844 });
  await openChat(page);
  await send(page, "q");
  await expect(page.getByTestId("assistant-message")).toBeVisible();
  expect(await noSidewaysScroll(page)).toBe(true);
  await expect(page.getByTestId("assistant-message").locator("pre").first()).toHaveCSS("overflow-x", "auto");
});

test("reduced motion stops every animation and keeps Working and Stop", async ({ page, host }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openChat(page);
  await send(page, "q");
  host.toolCall();
  await expect(page.getByTestId("composer-stop")).toBeVisible();
  await expect(page.getByText(/Working for \ds/).first()).toBeVisible();
  const moving = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("*")]
      .filter((el) => {
        const style = getComputedStyle(el);
        return style.animationName !== "none" && parseFloat(style.animationDuration) > 0 && style.animationIterationCount !== "1";
      })
      .map((el) => el.className.toString().slice(0, 60)),
  );
  expect(moving).toEqual([]);
});
