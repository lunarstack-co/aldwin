import type { Page } from "@playwright/test";

import { connectorFixture } from "./fixtures/settings-fixtures";
import { expect, openChat, session, test, type FakeHost } from "./fixtures/test";

const SECTIONS = ["Providers & models", "Search", "Skills", "Tools", "Advanced", "Appearance"];

async function openSettings(page: Page, section?: string): Promise<void> {
  await openChat(page, section ? `/settings/?section=${section}` : "/settings/");
  await expect(page.getByTestId("settings-hub")).toBeVisible();
}

const nav = (page: Page) => page.getByTestId("settings-nav");

// The page asks for lists while it starts; anything else sent after `from` would be a change.
const READS = ["hello", "status", "history", "todo.get", "sessions.list", "models.list", "search.list", "connectors.list", "skills.list"];
const changes = (host: FakeHost, from: number) => host.sent.slice(from).filter((f) => !READS.includes(String(f.type)));

test("the rail opens Settings with exactly six sections @cross", async ({ page }) => {
  await openChat(page);
  if ((page.viewportSize()?.width ?? 1280) < 768) await page.getByTestId("open-rail").click();
  await page.getByTestId("settings").filter({ visible: true }).click();
  await expect(page).toHaveURL(/\/settings\/$/);
  await expect(page).toHaveTitle("Settings · Aldwin");
  await expect(nav(page).getByRole("link")).toHaveText(SECTIONS);
  await expect(nav(page)).not.toContainText(/Profile|Notifications/);
});

test("the address names the open section", async ({ page }) => {
  await openSettings(page, "search");
  await expect(nav(page).getByRole("link", { name: "Search" })).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#settings-section-title")).toHaveText("Search");
});

test("an unknown section falls back to Providers & models", async ({ page }) => {
  await openSettings(page, "nope");
  await expect(page.locator("#settings-section-title")).toHaveText("Providers & models");
});

test("Back moves between sections", async ({ page }) => {
  await openSettings(page);
  await nav(page).getByRole("link", { name: "Search" }).click();
  await expect(page).toHaveURL(/section=search$/);
  await nav(page).getByRole("link", { name: "Tools" }).click();
  await expect(page).toHaveURL(/section=tools$/);
  await page.goBack();
  await expect(page).toHaveURL(/section=search$/);
  await expect(page.locator("#settings-section-title")).toHaveText("Search");
});

test("moving between chat and Settings keeps one socket and the open conversation", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  await openChat(page, "/c/?id=a");
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Alpha.");
  await page.getByTestId("settings").filter({ visible: true }).click();
  await expect(page.getByTestId("settings-hub")).toBeVisible();
  await expect(page).toHaveURL(/\/settings\/$/);
  expect(host.sentOfType("sessions.new")).toHaveLength(0);
  await page.getByRole("link", { name: "Back to chat" }).click();
  await expect(page).toHaveURL(/\/c\/\?id=a$/);
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Alpha.");
  expect(host.sockets).toHaveLength(1);
  expect(host.sentOfType("hello")).toHaveLength(1);
});

test("the rail marks Settings as current and still opens conversations", async ({ page, host }) => {
  host.sessions = [session("a", "Alpha")];
  await openSettings(page);
  await expect(page.getByTestId("settings").filter({ visible: true })).toHaveAttribute("aria-current", "page");
  await page.getByTestId("history-row").filter({ visible: true, hasText: "Alpha" }).click();
  await expect(page).toHaveURL(/\/c\/\?id=a$/);
  await expect(page.getByTestId("assistant-message")).toContainText("Notes on Alpha.");
});

test.describe("appearance", () => {
  test("theme changes stay in the browser @cross", async ({ page, host }) => {
    await openSettings(page, "appearance");
    const before = host.calls.length;
    const frames = host.sent.length;
    await page.getByTestId("appearance-theme-light").click();
    await expect(page.locator("html")).toHaveClass(/light/);
    await page.getByTestId("appearance-theme-dark").click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    expect(host.calls.length).toBe(before);
    expect(changes(host, frames)).toEqual([]);
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await expect(page.getByTestId("appearance-theme-dark")).toHaveAttribute("aria-checked", "true");
  });

  test("density is kept in the browser", async ({ page, host }) => {
    await openSettings(page, "appearance");
    const frames = host.sent.length;
    await page.getByTestId("appearance-density-detail").click();
    expect(await page.evaluate(() => Object.values({ ...localStorage }).includes("detail"))).toBe(true);
    expect(changes(host, frames)).toEqual([]);
  });
});

test.describe("advanced", () => {
  test("shows the host's limits", async ({ page }) => {
    await openSettings(page, "advanced");
    await expect(page.getByTestId("limit-context_exchanges")).toHaveValue("20");
    await expect(page.getByTestId("limit-max_steps")).toHaveValue("15");
    await expect(page.getByTestId("limit-connector_tools")).toHaveValue("40");
    await expect(page.getByTestId("check-for-updates")).toHaveAttribute("aria-checked", "true");
  });

  test("saving sends only the changed limit", async ({ page, host }) => {
    await openSettings(page, "advanced");
    await page.getByTestId("limit-max_steps").fill("20");
    await page.getByTestId("advanced-save").click();
    await expect(page.getByTestId("advanced-form")).toContainText("Saved");
    const patches = host.calls.filter((c) => c.method === "PATCH");
    expect(patches.map((c) => c.body)).toEqual([{ limits: { max_steps: 20 } }]);
    expect(host.settings.limits.max_steps).toBe(20);
  });

  test("a refused limit shows the host's reason by the field and changes nothing", async ({ page, host }) => {
    await openSettings(page, "advanced");
    await page.getByTestId("limit-max_steps").fill("0");
    await page.getByTestId("advanced-save").click();
    await expect(page.getByTestId("advanced-form")).toContainText("limits.max_steps: Input should be greater than or equal to 1.");
    await expect(page.getByTestId("limit-max_steps")).toHaveValue("0");
    await expect(page.getByTestId("limit-max_steps")).toHaveAttribute("aria-invalid", "true");
    expect(host.settings.limits.max_steps).toBe(15);
  });

  test("turning off update checks sends just that", async ({ page, host }) => {
    await openSettings(page, "advanced");
    await page.getByTestId("check-for-updates").click();
    await page.getByTestId("advanced-save").click();
    await expect.poll(() => host.calls.filter((c) => c.method === "PATCH").map((c) => c.body)).toEqual([{ check_for_updates: false }]);
  });

  test("with no changes Save is disabled", async ({ page, host }) => {
    await openSettings(page, "advanced");
    await expect(page.getByTestId("advanced-save")).toBeDisabled();
    expect(host.calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
  });
});

test.describe("providers", () => {
  const row = (page: Page, name: string) => page.locator(`[data-provider="${name}"]`);

  test("lists each provider with its status, logo and masked key", async ({ page }) => {
    await openSettings(page, "providers");
    await expect(row(page, "openai")).toContainText("Connected");
    await expect(row(page, "anthropic")).toContainText("Connected");
    await expect(row(page, "ollama")).toContainText("Not running");
    await expect(row(page, "mistral")).toContainText("Not set up");
    for (const name of ["openai", "anthropic", "ollama", "mistral"]) {
      await expect(row(page, name).locator(`svg[data-brand="${name}"]`)).toBeVisible();
    }
    // Keys never show on the rows, not even masked.
    await expect(page.getByTestId("provider-list")).not.toContainText(/sk-/);
  });

  async function enterKey(page: Page, key: string) {
    await row(page, "openai").getByRole("button", { name: "Replace key" }).click();
    await page.getByLabel("OpenAI API key").fill(key);
    await page.getByTestId("key-form-openai").getByRole("button", { name: /^(Connect|Try again)$/ }).click();
  }

  test("a key is checked and saved, and the form closes @cross", async ({ page, host }) => {
    await openSettings(page, "providers");
    await enterKey(page, "sk-good-123456");
    await expect(page.getByTestId("key-form-openai")).toHaveCount(0);
    await expect(row(page, "openai")).toContainText("Connected");
    const posts = host.calls.filter((c) => c.path === "/v1/settings/secrets");
    expect(posts.map((c) => c.body)).toEqual([{ kind: "provider", name: "openai", key: "sk-good-123456" }]);
  });

  test("a refused key shows the reason and keeps the text", async ({ page }) => {
    await openSettings(page, "providers");
    await enterKey(page, "sk-bad");
    await expect(page.getByTestId("key-form-openai")).toContainText("OpenAI refused this key.");
    await expect(page.getByLabel("OpenAI API key")).toHaveValue("sk-bad");
  });

  test("a provider that does not answer offers Try again", async ({ page }) => {
    await openSettings(page, "providers");
    await enterKey(page, "sk-slow");
    await expect(page.getByTestId("key-form-openai")).toContainText("OpenAI did not answer in time.");
    await expect(page.getByTestId("key-form-openai").getByRole("button", { name: "Try again" })).toBeVisible();
  });

  test("the full key is sent once and never comes back", async ({ page, host }) => {
    await openSettings(page, "providers");
    await enterKey(page, "sk-good-123456");
    await expect(page.getByTestId("key-form-openai")).toHaveCount(0);
    const key = "sk-good-123456";
    expect(host.calls.filter((c) => JSON.stringify(c.body ?? "").includes(key))).toHaveLength(1);
    expect(host.responses.filter((r) => JSON.stringify(r.body).includes(key))).toHaveLength(0);
    expect(host.sent.filter((f) => JSON.stringify(f).includes(key))).toHaveLength(0);
    expect(await page.locator("body").innerText()).not.toContain(key);
  });

  test("a server on this computer is added as local", async ({ page, host }) => {
    await openSettings(page, "providers");
    await page.getByTestId("add-server-open").click();
    await page.getByLabel("Name").fill("lmstudio");
    await page.getByLabel("Address").fill("http://127.0.0.1:1234/v1");
    await page.getByTestId("add-server").getByRole("button", { name: "Connect" }).click();
    await expect(row(page, "lmstudio")).toBeVisible();
    expect(host.sentOfType("provider.server")).toEqual([
      expect.objectContaining({ name: "lmstudio", base_url: "http://127.0.0.1:1234/v1", local: true }),
    ]);
  });

  test("disconnecting a provider sets it back to not set up", async ({ page, host }) => {
    await openSettings(page, "providers");
    await row(page, "anthropic").getByRole("button", { name: "Disconnect" }).click();
    await expect(row(page, "anthropic")).toContainText("Not set up");
    expect(host.sentOfType("provider.disconnect")).toEqual([expect.objectContaining({ provider: "anthropic" })]);
  });

  test("the current model shows its name and logo", async ({ page }) => {
    await openSettings(page, "providers");
    const picker = page.getByTestId("model-picker-settings");
    await expect(picker).toContainText("GPT-4o");
    await expect(picker.locator('svg[data-brand="openai"]')).toBeVisible();
  });
});

test.describe("search", () => {
  const row = (page: Page, name: string) => page.locator(`[data-engine="${name}"]`);

  test("lists the engines with their state and logos", async ({ page }) => {
    await openSettings(page, "search");
    await expect(row(page, "tavily")).toContainText("In use");
    await expect(row(page, "brave").getByRole("button", { name: "Connect" })).toBeVisible();
    await expect(row(page, "searxng")).toContainText("On this computer");
    for (const name of ["tavily", "brave", "searxng"]) await expect(row(page, name).locator(`svg[data-brand="${name}"]`)).toBeVisible();
    await expect(page.getByTestId("search-list")).not.toContainText("tvly-");
  });

  test("connecting Brave sends its key and shows it masked", async ({ page, host }) => {
    await openSettings(page, "search");
    await row(page, "brave").getByRole("button", { name: "Connect" }).click();
    await page.getByLabel("Brave API key").fill("brave-key-1234");
    await row(page, "brave").getByRole("button", { name: "Connect" }).click();
    await expect(row(page, "brave")).toContainText("Connected");
    await expect(row(page, "brave")).not.toContainText("key-");
    expect(host.sentOfType("search.connect")).toEqual([expect.objectContaining({ provider: "brave", key: "brave-key-1234" })]);
  });

  test("making Brave active leaves only Brave active", async ({ page, host }) => {
    host.search.items = host.search.items.map((i) => (i.name === "brave" ? { ...i, connected: true, source: "file", detail: "key-…9999" } : i));
    await openSettings(page, "search");
    await row(page, "brave").getByRole("button", { name: "Use this one" }).click();
    await expect(page.locator("[data-engine][data-active]")).toHaveCount(1);
    await expect(row(page, "brave")).toContainText("In use");
    expect(host.sentOfType("search.use")).toEqual([expect.objectContaining({ provider: "brave" })]);
  });

  test("disconnecting Tavily sends just that", async ({ page, host }) => {
    await openSettings(page, "search");
    await row(page, "tavily").getByRole("button", { name: "Disconnect" }).click();
    await expect(row(page, "tavily")).toContainText("Not connected");
    expect(host.sentOfType("search.disconnect")).toEqual([expect.objectContaining({ provider: "tavily" })]);
  });

  test("the SearXNG address is saved as a setting", async ({ page, host }) => {
    await openSettings(page, "search");
    await page.getByLabel("SearXNG address").fill("http://127.0.0.1:8080");
    await page.getByRole("button", { name: "Save address" }).click();
    await expect(page.getByTestId("searxng-form")).toContainText("Saved");
    expect(host.calls.filter((c) => c.method === "PATCH").map((c) => c.body)).toEqual([{ search: { searxng_url: "http://127.0.0.1:8080" } }]);
  });
});

test.describe("skills", () => {
  const row = (page: Page, name: string) => page.locator(`[data-skill="${name}"]`);

  test("lists the skills with source and last use, and flags a broken one", async ({ page }) => {
    await openSettings(page, "skills");
    await expect(page.getByTestId("skills-list").locator("[data-skill]")).toHaveCount(5);
    await expect(row(page, "ship-notes")).toContainText("Made by you");
    await expect(row(page, "files")).toContainText("Comes with Aldwin");
    await expect(row(page, "files")).toContainText("Last used");
    await expect(row(page, "memory")).toContainText("Not used yet");
    await expect(row(page, "broken-skill")).toContainText("Can't be used");
    await expect(row(page, "broken-skill")).toContainText("bad frontmatter");
    await expect(page.getByTestId("skills-list").getByRole("switch")).toHaveCount(0);
  });

  test("a skill's text is shown as formatted, safe Markdown", async ({ page, host }) => {
    const dialogs: string[] = [];
    page.on("dialog", (d) => void d.dismiss().then(() => dialogs.push(d.message())));
    host.skillBodies["ship-notes"] =
      "---\nname: ship-notes\n---\n# Ship notes\n\nWrite them **short**.\n\n- newest first\n\n<img src=x onerror=alert(1)>";
    await openSettings(page, "skills");
    await row(page, "ship-notes").getByRole("button", { name: "Show" }).click();
    const body = page.getByTestId("skill-body-ship-notes");
    await expect(body.getByRole("heading", { name: "Ship notes" })).toBeVisible();
    await expect(body.locator("strong")).toHaveText("short");
    await expect(body.getByRole("listitem")).toHaveText("newest first");
    await expect(body).not.toContainText("name: ship-notes");
    await expect(body.locator("img")).toHaveCount(0);
    expect(dialogs).toEqual([]);
    expect(host.sentOfType("skills.show")).toEqual([expect.objectContaining({ name: "ship-notes" })]);
  });
});

test.describe("tools", () => {
  const row = (page: Page, name: string) => page.locator(`[data-connector="${name}"]`);
  const panel = (page: Page) => page.getByTestId("connector-sign-in");

  async function authenticateNotion(page: Page, host: FakeHost) {
    await openSettings(page, "tools");
    await row(page, "notion").getByRole("button", { name: "Authenticate" }).click();
    await expect.poll(() => host.sentOfType("connectors.login").length).toBe(1);
  }

  test("lists connected apps and the catalogue with their marks", async ({ page }) => {
    await openSettings(page, "tools");
    await expect(row(page, "github")).toContainText("Connected");
    await expect(row(page, "github")).toContainText("12 tools");
    await expect(row(page, "notion")).toContainText("Needs you to sign in");
    for (const name of ["github", "notion"]) await expect(row(page, name).locator(`svg[data-brand="${name}"]`)).toBeVisible();
    await page.getByTestId("add-app-open").click();
    const catalog = page.getByTestId("add-app").getByTestId("connector-catalog");
    await expect(catalog.locator("[data-catalog]")).toHaveCount(2);
    for (const name of ["linear", "dropbox"]) await expect(catalog.locator(`[data-catalog="${name}"] svg[data-brand="${name}"]`)).toBeVisible();
  });

  test("turning an app off and on follows the host's status", async ({ page, host }) => {
    await openSettings(page, "tools");
    await page.getByTestId("connector-enabled-github").click();
    await expect(row(page, "github")).toContainText("Off");
    await page.getByTestId("connector-enabled-github").click();
    await expect(row(page, "github")).toContainText("Connected");
    expect(host.sent.filter((f) => String(f.type).match(/^connectors\.(en|dis)able$/)).map((f) => [f.type, f.name])).toEqual([
      ["connectors.disable", "github"],
      ["connectors.enable", "github"],
    ]);
  });

  test("removing an app asks first", async ({ page, host }) => {
    await openSettings(page, "tools");
    await row(page, "notion").getByRole("button", { name: "Remove" }).click();
    const dialog = page.getByTestId("remove-connector");
    await expect(dialog).toContainText("Remove Notion?");
    expect(host.sentOfType("connectors.remove")).toHaveLength(0);
    await dialog.getByRole("button", { name: "Remove" }).click();
    await expect(row(page, "notion")).toHaveCount(0);
    expect(host.sentOfType("connectors.remove")).toEqual([expect.objectContaining({ name: "notion" })]);
  });

  test("adding from the catalogue sends its address without opening a browser", async ({ page, host }) => {
    await openSettings(page, "tools");
    await page.getByTestId("add-app-open").click();
    await page.locator('[data-catalog="linear"]').getByRole("button", { name: "Add" }).click();
    await expect(page.getByTestId("add-app")).toHaveCount(0);
    await expect(row(page, "linear")).toBeVisible();
    expect(host.sentOfType("connectors.add")).toEqual([
      expect.objectContaining({ url: "https://mcp.linear.example.com/mcp", name: "linear", no_browser: true }),
    ]);
  });

  test("searching the catalogue narrows it and says when nothing matches", async ({ page }) => {
    await openSettings(page, "tools");
    await page.getByTestId("add-app-open").click();
    const dialog = page.getByTestId("add-app");
    await dialog.getByRole("searchbox", { name: "Search apps" }).fill("drop");
    await expect(dialog.locator("[data-catalog]")).toHaveCount(1);
    await expect(dialog.locator('[data-catalog="dropbox"]')).toBeVisible();
    await dialog.getByRole("searchbox", { name: "Search apps" }).fill("zzz");
    await expect(dialog).toContainText('No app matches "zzz"');
  });

  test("adding by address checks it first", async ({ page, host }) => {
    await openSettings(page, "tools");
    await page.getByTestId("add-app-open").click();
    const form = page.getByTestId("add-connector-url");
    await page.getByLabel("Server address").fill("notaurl");
    await form.getByRole("button", { name: "Add" }).click();
    await expect(form).toContainText("Enter a full address");
    expect(host.sentOfType("connectors.add")).toHaveLength(0);
    await page.getByLabel("Server address").fill("https://mcp.example.com/mcp");
    await form.getByRole("button", { name: "Add" }).click();
    await expect(page.getByTestId("add-app")).toHaveCount(0);
    await expect.poll(() => host.sentOfType("connectors.add")).toEqual([
      expect.objectContaining({ url: "https://mcp.example.com/mcp", auth: "none" }),
    ]);
  });

  test("browser sign-in opens the page only on a click @cross", async ({ page, host, context }) => {
    await authenticateNotion(page, host);
    expect(host.sentOfType("connectors.login")).toEqual([expect.objectContaining({ name: "notion", no_browser: true })]);
    let opened = 0;
    context.on("page", () => opened++);
    host.connectorAuth("notion", "browser", { url: "https://notion.example.com/authorize?x=1" });
    await expect(panel(page)).toContainText("Sign in to Notion");
    const open = page.getByTestId("sign-in-open");
    await expect(open).toHaveAttribute("href", "https://notion.example.com/authorize?x=1");
    expect(opened).toBe(0);
    host.finishSignIn("notion");
    await expect(panel(page)).toHaveCount(0);
    await expect(row(page, "notion")).toContainText("Connected");
  });

  test("paste sign-in sends the address the browser landed on", async ({ page, host }) => {
    await authenticateNotion(page, host);
    host.connectorAuth("notion", "paste", { url: "https://notion.example.com/authorize" });
    await page.getByLabel("Address after signing in").fill("http://127.0.0.1/callback?code=x");
    await panel(page).getByRole("button", { name: "Finish sign-in" }).click();
    await expect.poll(() => host.sentOfType("connectors.auth_paste")).toEqual([
      expect.objectContaining({ name: "notion", redirect_url: "http://127.0.0.1/callback?code=x" }),
    ]);
  });

  test("token sign-in never leaves the token on the page", async ({ page, host }) => {
    await authenticateNotion(page, host);
    host.connectorAuth("notion", "token", { token_help_url: "https://notion.example.com/tokens" });
    await page.getByLabel("Notion token").fill("secret-token-123");
    await panel(page).getByRole("button", { name: "Finish sign-in" }).click();
    await expect.poll(() => host.sentOfType("connectors.auth_token")).toEqual([
      expect.objectContaining({ name: "notion", token: "secret-token-123" }),
    ]);
    await expect(page.getByLabel("Notion token")).toHaveValue("");
    expect(await page.locator("body").innerText()).not.toContain("secret-token-123");
  });

  test("a sign-in that runs out of time closes and can start again", async ({ page, host }) => {
    await authenticateNotion(page, host);
    host.connectorAuth("notion", "browser", { url: "https://notion.example.com/authorize", expires_in: 1 });
    await expect(panel(page)).toBeVisible();
    await expect(panel(page)).toHaveCount(0, { timeout: 3000 });
    await expect(page.getByTestId("sign-in-expired")).toContainText("ran out of time");
    await expect(row(page, "notion").getByRole("button", { name: "Authenticate" })).toBeEnabled();
  });

  test("a hostile connector name is shown as text", async ({ page, host }) => {
    const dialogs: string[] = [];
    page.on("dialog", (d) => void d.dismiss().then(() => dialogs.push(d.message())));
    host.connectors.items.push(connectorFixture("evil", "<img src=x onerror=alert(1)>"));
    await openSettings(page, "tools");
    await expect(row(page, "evil")).toContainText("<img src=x onerror=alert(1)>");
    await expect(page.getByTestId("connector-list").locator("img")).toHaveCount(0);
    expect(dialogs).toEqual([]);
  });
});

test("brand marks keep their colours, black and white ones follow the theme", async ({ page }) => {
  await openSettings(page, "search");
  await expect(page.locator('[data-engine="brave"] svg[data-brand="brave"]')).toHaveAttribute("fill", "#FB542B");
  await openSettings(page, "providers");
  await expect(page.locator('[data-provider="openai"] svg[data-brand="openai"]')).toHaveAttribute("fill", "currentColor");
});

test("a dropped socket reloads the open section and frees a waiting sign-in", async ({ page, host }) => {
  await openSettings(page, "tools");
  await page.locator('[data-connector="notion"]').getByRole("button", { name: "Authenticate" }).click();
  await expect.poll(() => host.sentOfType("connectors.login").length).toBe(1);
  host.connectorAuth("notion", "browser", { url: "https://notion.example.com/authorize" });
  await expect(page.getByTestId("connector-sign-in")).toBeVisible();
  await host.drop();
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-phase", "open", { timeout: 10_000 });
  await expect.poll(() => host.sentOfType("connectors.list").length).toBeGreaterThanOrEqual(2);
  await expect(page.getByTestId("connector-sign-in")).toHaveCount(0);
  await expect(page.getByTestId("settings-tools")).not.toContainText("Loading");
  await expect(page.locator('[data-connector="notion"]').getByRole("button", { name: "Authenticate" })).toBeEnabled();
});

test("Settings makes no request off this computer", async ({ page }) => {
  const outside: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname)) outside.push(request.url());
  });
  for (const section of ["providers", "search", "skills", "tools", "advanced", "appearance"]) {
    await openSettings(page, section);
    await expect(page.locator(`[data-testid=settings-${section}]`)).not.toContainText("Loading");
  }
  expect(outside).toEqual([]);
});

test("the section list and Back to chat stay in view while a section scrolls", async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 1280) < 768, "phones show the sections as a row above");
  await page.setViewportSize({ width: 1280, height: 420 });
  await openSettings(page, "skills");
  await expect(page.getByTestId("skills-list").locator("[data-skill]")).toHaveCount(5);
  await page.getByTestId("settings-hub").evaluate((el) => el.scrollTo(0, el.scrollHeight));
  await expect(page.getByRole("link", { name: "Back to chat" })).toBeInViewport();
  await expect(nav(page).getByRole("link", { name: "Tools" })).toBeInViewport();
});
