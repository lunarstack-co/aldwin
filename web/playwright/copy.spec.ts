import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { expect, openChat, send, test } from "./fixtures/test";

function strings(value: unknown, into: Set<string>) {
  if (typeof value === "string") into.add(value);
  else if (value && typeof value === "object") Object.values(value).forEach((v) => strings(v, into));
}

function dataStrings(): string[] {
  const all = new Set<string>();
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (name.endsWith(".json")) strings(JSON.parse(readFileSync(path, "utf8")), all);
    }
  };
  walk(join(__dirname, "..", "src", "data"));
  return [...all];
}

/** A template like "Working for {seconds}s" as a pattern for its rendered text. */
function pattern(template: string): RegExp {
  const escaped = template.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\\\{\w+\\\}/g, ".+");
  return new RegExp(`^${escaped}$`);
}

test("visible strings live in data files", async ({ page, host }) => {
  host.answerNext("Fixture answer.");
  await openChat(page);
  await send(page, "Fixture question");
  await expect(page.getByTestId("message-actions")).toBeVisible();
  const known = dataStrings().map(pattern);
  const visible = await page.evaluate(() =>
    [...document.querySelectorAll("button, h1, h2, p, label, a, [role=status], [data-slot=badge]")]
      .filter((el) => el.getBoundingClientRect().width > 0)
      .map((el) => (el as HTMLElement).innerText.trim())
      .filter(Boolean),
  );
  const dynamic = /^(Fixture|Ada|openai\/gpt-4o|gpt-4o$|GPT-4o$|Tavily$|\d|Worked|Working)/;
  const unknown = visible
    .flatMap((text) => text.split("\n").map((line) => line.trim()).filter(Boolean))
    .filter((line) => !dynamic.test(line) && !known.some((re) => re.test(line)));
  expect(unknown).toEqual([]);
});

test("copy has no em dashes or filler words", () => {
  for (const text of dataStrings()) {
    expect(text, text).not.toMatch(/—/);
    expect(text, text).not.toMatch(/\b(simply|seamless|effortless)/i);
  }
});
