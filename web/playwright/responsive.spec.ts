import { expect, openChat, send, session, setDensity, test } from "./fixtures/test";

const WIDTHS = [320, 390, 768, 1024, 1280, 1920, 2560];
const ROUTES = ["/", "/c/?id=a", "/onboarding/name/", "/404.html"];

/** Opens `route`; the chat may give a conversation the same address at the same moment, which is fine. */
async function open(page: import("@playwright/test").Page, route: string) {
  try {
    await page.goto(route);
  } catch (error) {
    if (!String(error).includes("is interrupted by another navigation")) throw error;
  }
}

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
      await open(page, route);
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
  // The composer can take focus back as the page settles, and a key typed there is text, not a shortcut.
  await expect
    .poll(() =>
      page.evaluate(() => {
        (document.activeElement as HTMLElement | null)?.blur();
        return document.activeElement === document.body;
      }),
    )
    .toBe(true);
  await page.keyboard.press("ControlOrMeta+b");
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

test("Settings fits a phone with big targets @cross", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  for (const section of ["providers", "search", "skills", "tools", "advanced", "appearance"]) {
    await openChat(page, `/settings/?section=${section}`);
    await expect(page.locator(`[data-testid=settings-${section}]`)).not.toContainText("Loading");
    const wide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(wide, `${section} scrolls sideways`).toBe(false);
    const small = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>("[data-testid=settings-hub] :is(button, a, [role=button], [role=switch], input, summary)")]
        .filter((el) => {
          const r = el.getBoundingClientRect();
          const folded = el.closest("details:not([open])") !== null && el.tagName !== "SUMMARY";
          return r.width > 0 && r.height > 0 && !folded && !el.closest(".sr-only") && getComputedStyle(el).visibility !== "hidden";
        })
        .filter((el) => el.getAttribute("aria-hidden") !== "true" && !(el instanceof HTMLInputElement && el.type === "checkbox"))
        // A switch's label is its target.
        .filter((el) => !(el.getAttribute("role") === "switch" && (el.closest("label")?.getBoundingClientRect().height ?? 0) >= 40))
        // The show-key toggle sits inside its 40px field, which is the target.
        .filter((el) => !(el.hasAttribute("aria-pressed") && el.parentElement?.querySelector("input") !== null))
        // Inline text links sit in a sentence; everything else needs a 40px target.
        .filter((el) => el.getBoundingClientRect().height < 40 && !(el.tagName === "A" && !el.className.includes("inline-flex") && !el.closest("nav")))
        .map((el) => `${el.tagName}[${el.getAttribute("aria-label") ?? el.textContent?.trim().slice(0, 20)}] ${Math.round(el.getBoundingClientRect().height)}`),
    );
    expect(small, section).toEqual([]);
  }
});

test("on phones an empty chat keeps the composer at the bottom; wider screens centre it @cross", async ({ page }) => {
  await openChat(page);
  const composer = page.getByTestId("composer");
  await expect(composer).toBeVisible();
  const { height, width } = page.viewportSize() ?? { height: 720, width: 1280 };
  const box = (await composer.boundingBox())!;
  const gapBelow = height - (box.y + box.height);
  if (width < 768) expect(gapBelow).toBeLessThan(48);
  else expect(gapBelow).toBeGreaterThan(height / 5);
  // The greeting stays above the composer, in view.
  await expect(page.getByTestId("empty-home")).toBeInViewport();
});

test("approval cards, permission grants and displays fit 320 px and phones without sideways scroll @cross", async ({
  page,
  host,
}) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "phone check");

  host.toolCall({ call_id: "c_cmd", name: "run_command" });
  host.emitApproval({
    approval_id: "ap_phone",
    call_id: "c_cmd",
    tool: "run_command",
    summary: "Run long command",
    risky: ["recursive delete"],
    display: {
      kind: "terminal",
      command: `rm -rf ${"/very_long_folder_segment".repeat(6)}`,
      cwd: "/workspace",
      stdout: "",
      stderr: "",
      exit_code: null,
      duration_ms: 0,
      truncated: false,
    },
  });
  await expect(page.getByTestId("approval-card")).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
  ).toBe(true);

  await page.getByTestId("approval-card").getByRole("button", { name: "Allow", exact: true }).click();
  host.emitToolResult({
    call_id: "c_cmd",
    ok: true,
    summary: "exit 0",
    display: {
      kind: "terminal",
      command: "ls",
      cwd: "/ws",
      stdout: "done\n",
      stderr: "",
      exit_code: 0,
      duration_ms: 5,
      truncated: false,
    },
  });

  host.toolCall({ call_id: "c_grant", name: "read_file" });
  host.emitApproval({
    approval_id: "ap_grant_p",
    call_id: "c_grant",
    tool: "read_file",
    summary: "Read files in ~/Documents/tax",
    grantable: true,
    display: {
      kind: "grant",
      scope: "folder",
      reach: ["~/Documents/tax/2026/very/deep/nested/folder/path/for/mobile"],
    },
  });
  await expect(page.locator('[data-approval-id="ap_grant_p"]')).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth),
  ).toBe(true);
});
