import type { SessionMeta } from "@/types/protocol";

/** The title a row shows; a conversation the host has not named yet gets `untitled`. */
export function displayTitle(session: SessionMeta, untitled: string): string {
  return session.title.trim() || untitled;
}

export function filterSessions(sessions: SessionMeta[], query: string, untitled: string): SessionMeta[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return sessions;
  return sessions.filter((s) => displayTitle(s, untitled).toLocaleLowerCase().includes(needle));
}

export type SessionGroups = {
  today: SessionMeta[];
  yesterday: SessionMeta[];
  previous7: SessionMeta[];
  older: SessionMeta[];
};

const DAY_MS = 86_400_000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** Splits conversations by local calendar day of last use, keeping their order. */
export function groupSessions(sessions: SessionMeta[], now: Date): SessionGroups {
  const today = startOfDay(now);
  const groups: SessionGroups = { today: [], yesterday: [], previous7: [], older: [] };
  for (const session of sessions) {
    const day = startOfDay(new Date(session.updated_at));
    if (day >= today) groups.today.push(session);
    else if (day >= today - DAY_MS) groups.yesterday.push(session);
    else if (day >= today - 7 * DAY_MS) groups.previous7.push(session);
    else groups.older.push(session);
  }
  return groups;
}
