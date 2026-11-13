/** Keys are sent once and only ever come back masked. */
import type {
  LocalRuntime,
  ModelOption,
  ProfileUpdate,
  ProviderOption,
  SetupStatus,
} from "@/types/setup";

import { call } from "./client";

export function getSetupStatus(): Promise<SetupStatus> {
  return call("GET", "/v1/setup/status");
}

export function saveProfile(body: ProfileUpdate): Promise<SetupStatus> {
  return call("POST", "/v1/setup/profile", body);
}

export function saveProviderKey(name: string, key: string): Promise<{ masked: string }> {
  return call("POST", "/v1/setup/provider-key", { name, key });
}

export function connectServer(
  name: string,
  baseUrl: string,
  key?: string,
): Promise<{ masked: string }> {
  return call("POST", "/v1/setup/provider-key", {
    name,
    base_url: baseUrl,
    ...(key ? { key } : {}),
  });
}

export async function detectLocal(): Promise<LocalRuntime[]> {
  const body = await call<{ items: LocalRuntime[] }>("POST", "/v1/setup/detect-local");
  return body.items;
}

export async function listModels(provider: string): Promise<ModelOption[]> {
  const body = await call<{ items: ModelOption[] }>("POST", "/v1/setup/models", { provider });
  return body.items;
}

export function chooseModel(model: string): Promise<SetupStatus> {
  return call("POST", "/v1/setup/save", { model });
}

export async function listKeyProviders(): Promise<ProviderOption[]> {
  const body = await call<{ keys: (ProviderOption & { kind: string })[] }>(
    "GET",
    "/v1/settings",
  );
  return body.keys
    .filter((row) => row.kind === "provider")
    .map(({ name, title }) => ({ name, title }));
}
