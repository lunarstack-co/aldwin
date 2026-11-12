import type { Limits, LimitField } from "@/types/settings";

/** The six Settings sections, in the order the hub lists them. */
export const SETTINGS_SECTIONS = ["providers", "search", "skills", "tools", "advanced", "appearance"] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

/** The section an address names, or Providers & models when it names none or an unknown one. */
export function sectionOf(value: string | null): SettingsSection {
  return SETTINGS_SECTIONS.find((section) => section === value) ?? "providers";
}

/** Only the limits that differ, so a save sends just what the user changed. */
export function changedLimits(saved: Limits, edited: Limits): Partial<Limits> {
  const changed: Partial<Limits> = {};
  for (const field of Object.keys(edited) as LimitField[]) {
    if (edited[field] !== saved[field]) changed[field] = edited[field];
  }
  return changed;
}
