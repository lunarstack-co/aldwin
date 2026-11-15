import type { LimitField, LimitRule, Limits } from "@/types/settings";

export const SETTINGS_SECTIONS = ["providers", "search", "skills", "tools", "advanced", "appearance", "about"] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

/** Models when the address names no section or an unknown one. */
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

/** A whole number inside the range the host allows. */
export function limitIsValid(value: number, rule: LimitRule): boolean {
  return Number.isInteger(value) && value >= rule.min && value <= rule.max;
}

/** "4,000 characters", or "60%" for a percentage. */
export function withUnit(value: number, unit: string): string {
  const amount = value.toLocaleString("en");
  return unit === "%" ? `${amount}%` : `${amount} ${unit}`;
}
