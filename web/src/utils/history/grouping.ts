import type { SessionMeta } from "@/types/protocol";

/** The title a row shows; a conversation the host has not named yet gets `untitled`. */
export function displayTitle(session: SessionMeta, untitled: string): string {
  return session.title.trim() || untitled;
}

/** The conversations whose shown title contains `query`, ignoring case; all of them for a blank query. */
export function filterSessions(sessions: SessionMeta[], query: string, untitled: string): SessionMeta[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return sessions;
  return sessions.filter((s) => displayTitle(s, untitled).toLocaleLowerCase().includes(needle));
}

export type SessionGroups = { today: SessionMeta[]; earlier: SessionMeta[] };

/** Splits conversations by whether they were last used on `now`'s calendar day, in local time. */
export function groupSessions(sessions: SessionMeta[], now: Date): SessionGroups {
  const today = now.toDateString();
  const groups: SessionGroups = { today: [], earlier: [] };
  for (const session of sessions) {
    const day = new Date(session.updated_at).toDateString();
    (day === today ? groups.today : groups.earlier).push(session);
  }
  return groups;
}
