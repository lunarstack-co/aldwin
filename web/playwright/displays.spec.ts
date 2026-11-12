import type { FakeHost } from "./fixtures/fake-host";
import { expect, openChat, send, session, setDensity, test } from "./fixtures/test";

test("every display kind renders in detail density with safe links and no off-origin requests @cross", async ({
  page,
  host,
}) => {
  const outside: string[] = [];
  page.on("request", (req) => {
    if (!req.url().startsWith("http://127.0.0.1")) outside.push(req.url());
  });

  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "run everything");

  host.toolCall({ call_id: "c_term", name: "run_command" });
  host.emitToolResult({
    call_id: "c_term",
    ok: false,
    summary: "exit 3",
    duration_ms: 210,
    display: {
      kind: "terminal",
      command: "pytest -q",
      cwd: "/ws",
      stdout: "1 failed\n",
      stderr: "",
      exit_code: 3,
      duration_ms: 210,
      truncated: false,
    },
  });

  host.toolCall({ call_id: "c_diff", name: "edit_file" });
  host.emitToolResult({
    call_id: "c_diff",
    ok: true,
    summary: "Edit app.py",
    display: {
      kind: "diff",
      path: "app.py",
      additions: 2,
      deletions: 1,
      lines: [
        { kind: "removed", text: "old" },
        { kind: "added", text: "new1" },
        { kind: "added", text: "new2" },
      ],
      truncated: false,
    },
  });

  host.toolCall({ call_id: "c_files", name: "list_dir" });
  host.emitToolResult({
    call_id: "c_files",
    ok: true,
    summary: "List .",
    display: {
      kind: "files",
      root: ".",
      entries: Array.from({ length: 300 }, (_, i) =>
        i === 0
          ? { path: "src", kind: "folder" as const }
          : i === 1
            ? { path: "src/main.py", kind: "file" as const }
            : { path: `file_${i}.txt`, kind: "file" as const },
      ),
      truncated: true,
    },
  });

  host.toolCall({ call_id: "c_search", name: "web_search" });
  host.emitToolResult({
    call_id: "c_search",
    ok: true,
    summary: "2 results",
    display: {
      kind: "search",
      query: "aldwin release",
      results: [
        { title: "Aldwin Docs", url: "https://askaldwin.com/docs", snippet: "Local assistant." },
        { title: "Agentling", url: "https://agentling.dev", snippet: "Agent loop." },
      ],
    },
  });

  host.toolCall({ call_id: "c_page", name: "web_fetch" });
  host.emitToolResult({
    call_id: "c_page",
    ok: true,
    summary: "1k chars",
    display: {
      kind: "page",
      url: "https://example.com/article",
      title: "Example Article",
      site: "example.com",
      description: "Overview of the architecture.",
    },
  });

  host.toolCall({ call_id: "c_table", name: "read_document" });
  host.emitToolResult({
    call_id: "c_table",
    ok: true,
    summary: "2 rows",
    display: {
      kind: "table",
      columns: [
        { key: "item", label: "Item" },
        { key: "qty", label: "Qty" },
      ],
      rows: [
        { item: "Zebra", qty: "2" },
        { item: "Apple", qty: "9" },
      ],
      truncated: false,
    },
  });

  host.toolCall({ call_id: "c_mem", name: "remember" });
  host.emitToolResult({
    call_id: "c_mem",
    ok: true,
    summary: "Remember · saved",
    display: {
      kind: "memory",
      changes: [
        { text: "Likes tea", change: "added" },
        { text: "Uses Neovim", change: "replaced" },
        { text: "Old address", change: "removed" },
      ],
    },
  });

  host.toolCall({ call_id: "c_den", name: "run_command" });
  host.emitToolResult({
    call_id: "c_den",
    ok: false,
    summary: "blocked: rm_root_or_system",
    display: {
      kind: "denied",
      rule: "blocked_command",
      message: "Not run: this command is never allowed.",
    },
  });

  host.done();

  const term = page.getByTestId("display-terminal");
  await expect(term).toContainText("pytest -q");
  await expect(term).toContainText("1 failed");
  await expect(term).toContainText("Exit code 3");
  await expect(term).toContainText("210ms");

  const diff = page.getByTestId("display-diff");
  await expect(diff).toContainText("app.py");
  await expect(diff).toContainText("+2");
  await expect(diff).toContainText("−1");

  const files = page.getByTestId("display-files");
  await expect(files).toContainText("Showing the first 300");
  await expect(files).toContainText("main.py");
  await files.getByRole("button", { name: "src" }).click();
  await expect(files).not.toContainText("main.py");
  await files.getByRole("button", { name: "src" }).click();
  await expect(files).toContainText("main.py");

  const search = page.getByTestId("display-search");
  await expect(search).toContainText("aldwin release");
  await expect(search).toContainText("Aldwin Docs");
  await expect(search).toContainText("askaldwin.com");

  const pageCard = page.getByTestId("display-page");
  await expect(pageCard).toContainText("Example Article");
  await expect(pageCard).toContainText("example.com");

  const table = page.getByTestId("display-table");
  // Narrow screens get a list of rows instead of a sortable table.
  if ((page.viewportSize()?.width ?? 1280) < 768) {
    await expect(table.getByRole("listitem").first()).toBeVisible();
  } else {
    await table.getByRole("button", { name: /Item/ }).click();
    await expect(table.locator("tbody tr").first()).toContainText("Apple");
  }

  const mem = page.getByTestId("display-memory");
  await expect(mem).toContainText("Added");
  await expect(mem).toContainText("Replaced");
  await expect(mem).toContainText("Removed");

  const denied = page.getByTestId("display-denied");
  await expect(denied).toContainText("blocked_command");

  for (const container of [search, pageCard]) {
    await expect(container.locator("img")).toHaveCount(0);
    for (const link of await container.locator("a").all()) {
      await expect(link).toHaveAttribute("target", "_blank");
      await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    }
  }
  expect(outside).toEqual([]);
});

function emitTerminal(host: FakeHost, callId: string, command: string, stdout: string, exitCode = 0) {
  host.toolCall({ call_id: callId, name: "run_command" });
  host.emitToolResult({
    call_id: callId,
    ok: exitCode === 0,
    summary: `exit ${exitCode}`,
    display: {
      kind: "terminal",
      command,
      cwd: "/ws",
      stdout,
      stderr: "",
      exit_code: exitCode,
      duration_ms: 10,
      truncated: false,
    },
  });
}

const numbered = (prefix: string, count: number) =>
  `${Array.from({ length: count }, (_, i) => `${prefix}-${i + 1}`).join("\n")}\n`;

test("terminal output shows 20 lines in full and folds 21 behind Show all", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "check lines");
  emitTerminal(host, "c20", "seq 20", numbered("row", 20));
  emitTerminal(host, "c21", "seq 21", numbered("item", 21));
  host.done();

  const term20 = page.getByTestId("display-terminal").nth(0);
  await expect(term20).toContainText("row-20");
  await expect(term20.getByRole("button", { name: /Show all/ })).toHaveCount(0);

  const term21 = page.getByTestId("display-terminal").nth(1);
  await expect(term21).not.toContainText("item-21");
  await term21.getByRole("button", { name: /Show all/ }).click();
  await expect(term21).toContainText("item-21");
});

test("in clean density displays stay in the folded work chip until it opens", async ({ page, host }) => {
  await openChat(page);
  await send(page, "check lines");
  emitTerminal(host, "c1", "seq 2", numbered("row", 2));
  host.done();

  await expect(page.getByTestId("display-terminal")).toHaveCount(0);
  await page.getByTestId("tool-group").last().getByRole("button").first().click();
  await expect(page.getByTestId("display-terminal")).toHaveCount(1);
});

test("a failed tool without a display shows Tool failure and offers no retry", async ({ page, host }) => {
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "read it");
  host.toolCall({ call_id: "c_err", name: "read_file" });
  host.emitToolResult({
    call_id: "c_err",
    ok: false,
    summary: "not found: gone.txt",
    preview: "file missing on disk",
    display: null,
  });
  host.done();

  const failure = page.getByTestId("tool-failure");
  await expect(failure).toContainText("read_file");
  await expect(failure).toContainText("not found: gone.txt");
  await expect(failure).toContainText("file missing on disk");
  await expect(failure.getByRole("button", { name: /Retry/ })).toHaveCount(0);
});

test("a failed command keeps its terminal block and shows HTML in its output as text", async ({ page, host }) => {
  const dialogs: string[] = [];
  page.on("dialog", (d) => {
    dialogs.push(d.message());
    void d.dismiss();
  });
  await openChat(page);
  await setDensity(page, "detail");
  await send(page, "run it");
  emitTerminal(host, "c_fail", "echo '<img src=x onerror=alert(1)>'; exit 1", "<img src=x onerror=alert(1)>\n", 1);
  host.done();

  const term = page.getByTestId("display-terminal");
  await expect(term).toContainText("Exit code 1");
  await expect(term).toContainText("<img src=x onerror=alert(1)>");
  await expect(term.locator("img")).toHaveCount(0);
  await expect(page.getByTestId("tool-failure")).toHaveCount(0);
  expect(dialogs).toEqual([]);
});

test("after a reload past tool rows are plain, because displays are live only", async ({ page, host }) => {
  host.sessions = [session("s1", "Displays")];
  host.current = host.sessions[0]!;
  host.histories.set("s1", [
    { role: "user", text: "past" },
    { role: "tool", text: "✓ run_command · exit 0" },
    { role: "assistant", text: "done" },
  ]);
  await openChat(page, "/c/?id=s1");
  await setDensity(page, "detail");
  await expect(page.getByTestId("tool-card").first()).toContainText("run_command");
  await expect(page.getByTestId("display-terminal")).toHaveCount(0);
});
