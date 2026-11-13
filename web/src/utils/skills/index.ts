import type { DirectiveFormatter, DirectiveSegment, TriggerItem } from "@assistant-ui/react";

import type { SkillCatalogItem } from "@/types/protocol";

/** Matches `@skill` as the CLI does. `@types/node` and `@acme.com` are not skills. */
export const SKILL_TOKEN_RE = /(?<=^|\s)@([a-z0-9]+(?:-[a-z0-9]+)*)(?=$|[\s,;:!?)\]]|\.(?:\s|$))/g;

/** Same limit as the CLI picker. */
export const MAX_SKILL_ROWS = 8;

export const SKILL_ITEM_TYPE = "skill";

export function skillsFromText(text: string): string[] {
  const names = [...text.matchAll(SKILL_TOKEN_RE)].map((match) => match[1]);
  return [...new Set(names)];
}

function isBroken(item: SkillCatalogItem): boolean {
  return item.flags.some((flag) => flag.startsWith("error:"));
}

/** Skills the menu offers: none that failed to load, most recently used first, then by name. */
export function visibleSkills(items: readonly SkillCatalogItem[]): SkillCatalogItem[] {
  return items
    .filter((item) => !isBroken(item))
    .sort((a, b) => {
      if (a.last_used !== b.last_used) {
        if (a.last_used === null) return 1;
        if (b.last_used === null) return -1;
        return b.last_used.localeCompare(a.last_used);
      }
      return a.name.localeCompare(b.name);
    });
}

/** Names starting with the query first, then names containing it, capped at the row limit. */
export function searchSkills(items: readonly SkillCatalogItem[], query: string): SkillCatalogItem[] {
  const q = query.toLowerCase();
  const visible = visibleSkills(items);
  const starts = visible.filter((item) => item.name.startsWith(q));
  const contains = visible.filter((item) => !item.name.startsWith(q) && item.name.includes(q));
  return [...starts, ...contains].slice(0, MAX_SKILL_ROWS);
}

export function toTriggerItem(item: SkillCatalogItem): TriggerItem {
  return { id: item.name, type: SKILL_ITEM_TYPE, label: item.name, description: item.description };
}

/** `@name` is the form the host and the CLI read. */
export const skillFormatter: DirectiveFormatter = {
  serialize: (item) => `@${item.id}`,
  parse(text) {
    const segments: DirectiveSegment[] = [];
    let last = 0;
    for (const match of text.matchAll(SKILL_TOKEN_RE)) {
      if (match.index > last) segments.push({ kind: "text", text: text.slice(last, match.index) });
      segments.push({ kind: "mention", type: SKILL_ITEM_TYPE, id: match[1], label: match[1] });
      last = match.index + match[0].length;
    }
    if (last < text.length || segments.length === 0) segments.push({ kind: "text", text: text.slice(last) });
    return segments;
  },
};
