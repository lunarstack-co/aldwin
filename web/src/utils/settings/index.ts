import type { Limits, LimitField } from "@/types/settings";

export const SETTINGS_SECTIONS = ["providers", "search", "skills", "tools", "advanced", "appearance"] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

/** Providers & models when the address names no section or an unknown one. */
export function sectionOf(value: string | null): SettingsSection {
  return SETTINGS_SECTIONS.find((section) => section === value) ?? "providers";
}

/** Only changed limits, so a save sends just the edits. */
export function changedLimits(saved: Limits, edited: Limits): Partial<Limits> {
  const changed: Partial<Limits> = {};
  for (const field of Object.keys(edited) as LimitField[]) {
    if (edited[field] !== saved[field]) changed[field] = edited[field];
  }
  return changed;
}
