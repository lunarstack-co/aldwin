/** Keys only ever come back masked. */

export type KeyKind = "provider" | "search";

export type KeyRow = {
  kind: KeyKind;
  name: string;
  title: string;
  source: "env" | "file" | "missing";
  /** `sk-…a1b2`, or empty when no key is set. */
  masked: string;
};

export const LIMIT_FIELDS = [
  "context_exchanges",
  "profile_chars",
  "notes_chars",
  "notes_days",
  "persona_chars",
  "max_steps",
  "web_searches",
  "connector_tools",
] as const;

export type LimitField = (typeof LIMIT_FIELDS)[number];
export type Limits = Record<LimitField, number>;

export type SettingsView = {
  model: string;
  check_for_updates: boolean;
  limits: Limits;
  search: { provider: string; searxng_url: string | null };
  keys: KeyRow[];
};

/** Theme lives in the browser, so it is never one of these. */
export type SettingsPatch = {
  model?: string;
  limits?: Partial<Limits>;
  search?: { provider?: string; searxng_url?: string };
  check_for_updates?: boolean;
};

export type SavedKey = { ok: true; kind: KeyKind; name: string; masked: string; env_override: boolean };
