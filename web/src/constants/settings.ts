import type { LimitField } from "@/types/settings";

/** The Advanced page's sections, in order, and the limits each one holds. */
export const LIMIT_GROUPS: readonly { id: "reads" | "chats" | "turns"; fields: readonly LimitField[] }[] = [
  { id: "reads", fields: ["profile_chars", "persona_chars", "notes_chars", "notes_days"] },
  { id: "chats", fields: ["context_compact_percent"] },
  { id: "turns", fields: ["max_steps", "web_searches", "connector_tools"] },
];

/** The slider for when to summarise moves in steps of this many percent. */
export const PERCENT_STEP = 5;

/** How long the save bar says "Saved" before it goes away. */
export const SAVED_MS = 2000;
