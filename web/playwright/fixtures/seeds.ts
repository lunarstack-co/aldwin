import { session, type SessionRow } from "./fake-host";

/** 25 conversations over three weeks, newest first; s07 was saved mid-answer. */
export function sessions25(): SessionRow[] {
  const titles = ["Plan the Lisbon trip", "Planning taxes", "Groceries", ""];
  const base = new Date("2026-11-11T09:00:00Z").getTime();
  return Array.from({ length: 25 }, (_, i) => {
    const id = `s${String(i + 1).padStart(2, "0")}`;
    const title = i < titles.length ? titles[i] : `Note ${i + 1}`;
    const updated = new Date(base - i * 20 * 3_600_000).toISOString();
    return session(id, title, updated, id === "s07");
  });
}
