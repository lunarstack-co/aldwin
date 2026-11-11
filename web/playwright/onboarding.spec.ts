import { expect, test } from "./fixtures/test";

test.beforeEach(({ host }) => {
  host.status = { ...host.status, keys_ok: false, host_ready: false, profile_done: false };
});

test("no keys always lands on connect", async ({ page, host }) => {
  for (const path of ["/", "/c/?id=a", "/onboarding/name/"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/onboarding\/connect\/$/);
  }
  expect(host.sockets).toHaveLength(0);
  expect(host.calls.filter((c) => c.path === "/v1/ws-ticket")).toHaveLength(0);
});

test("only profile steps can be skipped", async ({ page, host }) => {
  await page.goto("/onboarding/connect/");
  await expect(page.getByRole("button", { name: /skip/i })).toHaveCount(0);
  const steps = page.getByTestId("onboarding-steps");
  await expect(steps).toContainText("Connect a model");
  await expect(steps.locator("[aria-current=step]")).toContainText("Connect a model");
  await expect(steps.locator("[aria-current=step]")).toContainText("Required");
  host.status = { ...host.status, keys_ok: true, host_ready: true };
  for (const path of ["/onboarding/name/", "/onboarding/occupation/", "/onboarding/personality/"]) {
    await page.goto(path);
    await expect(page.getByRole("button", { name: "Skip for now" })).toBeVisible();
    await expect(page.getByTestId("onboarding-steps").locator("[aria-current=step]")).toContainText("Optional");
  }
});

test("a good key waits for aldwin then asks about you", async ({ page, host }) => {
  let saved = false;
  host.rest.set("POST /v1/setup/provider-key", async (route) => {
    saved = true;
    await route.fulfill({ json: { masked: "sk-…abcd" } });
  });
  host.rest.set("GET /v1/setup/status", async (route) => {
    await route.fulfill({ json: saved ? { ...host.status, keys_ok: true, host_ready: true } : host.status });
  });
  await page.goto("/onboarding/connect/");
  await page.getByRole("textbox", { name: "API key" }).fill("sk-test-abcd");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page).toHaveURL(/\/onboarding\/name\/$/);
  const posted = host.calls.find((c) => c.path === "/v1/setup/provider-key");
  expect(posted?.body).toEqual({ name: "openai", key: "sk-test-abcd" });
});

for (const [status, reason] of [
  [402, "That key has no credit left. Add credit, then try again."],
  [429, "OpenAI is rate limiting this key. Wait a minute, then try again."],
  [504, "OpenAI did not answer in time. Check your connection, then try again."],
] as const) {
  test(`a refused key explains why (${status})`, async ({ page, host }) => {
    host.rest.set("POST /v1/setup/provider-key", (route) => route.fulfill({ status, json: { detail: reason } }));
    await page.goto("/onboarding/connect/");
    await page.getByRole("textbox", { name: "API key" }).fill("sk-bad");
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: reason })).toBeVisible();
    await expect(page).toHaveURL(/\/onboarding\/connect\/$/);
  });
}

test("api key is hidden until revealed", async ({ page }) => {
  await page.goto("/onboarding/connect/");
  const key = page.getByRole("textbox", { name: "API key" });
  await expect(key).toHaveAttribute("type", "password");
  const reveal = page.getByRole("button", { name: "Show key" });
  await reveal.click();
  await expect(key).toHaveAttribute("type", "text");
  await expect(page.getByRole("button", { name: "Hide key" })).toHaveAttribute("aria-pressed", "true");
});

test("connect paths are radio cards that move with arrows", async ({ page }) => {
  await page.goto("/onboarding/connect/");
  await page.getByRole("radio").first().focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("radio").nth(2)).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("Address")).toBeVisible();
});

test("model list filters by typing", async ({ page, host }) => {
  host.rest.set("POST /v1/setup/provider-key", (route) => route.fulfill({ json: { masked: "x" } }));
  host.rest.set("POST /v1/setup/models", (route) =>
    route.fulfill({ json: { items: [{ id: "gpt-4o", title: "GPT-4o", local: false }, { id: "gpt-4o-mini", title: "GPT-4o mini", local: false }] } }),
  );
  // Status keeps saying no key works, so after saving the page offers the models.
  await page.goto("/onboarding/connect/");
  await page.getByRole("textbox", { name: "API key" }).fill("sk");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  const combo = page.getByTestId("model-combobox");
  await expect(combo).toBeVisible({ timeout: 15_000 });
  await combo.fill("mini");
  await expect(page.getByRole("option")).toHaveText(["GPT-4o mini"]);
});

test.describe("profile", () => {
  test.beforeEach(({ host }) => {
    host.status = { ...host.status, keys_ok: true, host_ready: true, profile_done: false };
  });

  test("profile can be skipped from its first page", async ({ page, host }) => {
    await page.goto("/onboarding/name/");
    await page.getByRole("button", { name: "Skip for now" }).click();
    await expect(page.getByTestId("aldwin-chat-shell")).toBeVisible();
    expect(host.calls.find((c) => c.path === "/v1/setup/profile")?.body).toEqual({ skipped: true });
  });

  test("answers survive moving back and forth between pages", async ({ page }) => {
    const back = page.getByRole("link", { name: "Back" });
    const next = page.getByRole("button", { name: "Continue" });
    await page.goto("/onboarding/name/");
    await page.getByLabel("First name").fill("Ada");
    await next.click();
    await page.getByLabel("Occupation").fill("Engineer");
    await back.click();
    await expect(page).toHaveURL(/\/onboarding\/name\/$/);
    await expect(page.getByLabel("First name")).toHaveValue("Ada");
    await next.click();
    await expect(page.getByLabel("Occupation")).toHaveValue("Engineer");
    await next.click();
    const styles = page.getByRole("group").getByRole("button");
    await styles.nth(0).click();
    await styles.nth(2).click();
    await back.click();
    await expect(page.getByLabel("Occupation")).toHaveValue("Engineer");
    await next.click();
    await expect(styles.nth(0)).toHaveAttribute("aria-pressed", "true");
    await expect(styles.nth(2)).toHaveAttribute("aria-pressed", "true");
    await page.goBack();
    await page.goForward();
    await expect(styles.nth(0)).toHaveAttribute("aria-pressed", "true");
    await expect(styles.nth(1)).toHaveAttribute("aria-pressed", "false");
    await expect(styles.nth(2)).toHaveAttribute("aria-pressed", "true");
  });

  test("answer styles post as a list", async ({ page, host }) => {
    await page.goto("/onboarding/personality/");
    const group = page.getByRole("group");
    await group.getByRole("button").nth(0).click();
    await group.getByRole("button").nth(1).click();
    await expect(group.getByRole("button").nth(0)).toHaveAttribute("aria-pressed", "true");
    await page.getByRole("button", { name: /finish|continue|done/i }).first().click();
    await expect.poll(() => host.calls.filter((c) => c.path === "/v1/setup/profile").length).toBeGreaterThan(0);
    const body = host.calls.filter((c) => c.path === "/v1/setup/profile").at(-1)?.body as { personality?: string[] };
    expect(Array.isArray(body.personality)).toBe(true);
    expect(body.personality).toHaveLength(2);
  });
});

test("reloading an onboarding page waits in the onboarding frame", async ({ page, host }) => {
  host.status = { ...host.status, keys_ok: true, host_ready: true, profile_done: false };
  host.rest.set("GET /v1/setup/status", async (route) => {
    await new Promise((r) => setTimeout(r, 800));
    await route.fulfill({ json: host.status });
  });
  await page.goto("/onboarding/occupation/");
  const loading = page.getByTestId("aldwin-gates-loading");
  await expect(loading).toHaveAttribute("data-shape", "onboarding");
  await expect(page.getByTestId("onboarding-steps").locator("[aria-current=step]")).toContainText("Your work");
  await expect(page.getByLabel("Occupation")).toBeVisible();
  await expect(loading).toHaveCount(0);
});

for (const path of ["/", "/onboarding/name/"]) {
  test(`a fast reload of ${path} never shows the placeholder`, async ({ page, host }) => {
    host.status = { ...host.status, keys_ok: true, host_ready: true, profile_done: path === "/" };
    await page.addInitScript(() => {
      const w = window as unknown as { __placeholder: boolean };
      w.__placeholder = false;
      new MutationObserver(() => {
        if (document.querySelector("[data-testid=gates-placeholder]")) w.__placeholder = true;
      }).observe(document, { subtree: true, childList: true });
    });
    await page.goto(path);
    await page.reload();
    await expect(page.getByTestId("aldwin-gates-loading")).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __placeholder: boolean }).__placeholder)).toBe(false);
  });
}

test("loading then chat when the gates clear", async ({ page, host }) => {
  host.status = { ...host.status, keys_ok: true, host_ready: true, profile_done: true };
  host.rest.set("GET /v1/setup/status", async (route) => {
    await new Promise((r) => setTimeout(r, 800));
    await route.fulfill({ json: host.status });
  });
  await page.goto("/");
  const loading = page.getByTestId("aldwin-gates-loading");
  await expect(loading).toHaveAttribute("role", "status");
  await expect(loading).toHaveAttribute("data-shape", "chat");
  await expect(loading.locator("[data-brand-mark]")).toHaveCSS("width", "32px");
  await expect(page.getByTestId("aldwin-gates-clear")).toBeVisible();
});
