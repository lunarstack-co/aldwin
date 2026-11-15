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
  "context_compact_percent",
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

/** A limit's default and the range the settings page allows, both decided by the host. */
export type LimitRule = { default: number; min: number; max: number };

export type SettingsView = {
  model: string;
  check_for_updates: boolean;
  limits: Limits;
  limit_rules: Record<LimitField, LimitRule>;
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

/** Where Aldwin keeps its files, and which version is running. */
export type HomeInfo = { home: string; version: string };
