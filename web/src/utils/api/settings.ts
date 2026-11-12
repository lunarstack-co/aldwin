import type { KeyKind, SavedKey, SettingsPatch, SettingsView } from "@/types/settings";

import { call } from "./client";

export function getSettings(): Promise<SettingsView> {
  return call("GET", "/v1/settings");
}

/** Changes only the given fields; the host refuses the whole patch if one field is wrong. */
export function patchSettings(patch: SettingsPatch): Promise<SettingsView> {
  return call("PATCH", "/v1/settings", patch);
}

/** The host checks the key with its vendor first and saves it only if the check passes. */
export function saveSecret(kind: KeyKind, name: string, key: string): Promise<SavedKey> {
  return call("POST", "/v1/settings/secrets", { kind, name, key });
}
