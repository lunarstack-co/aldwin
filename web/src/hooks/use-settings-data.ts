"use client";

import { useEffect } from "react";

import { useSession } from "@/providers/chat-session-provider";
import { useConnectionStore } from "@/state/connection-store";
import { useSettingsStore, type Loaded } from "@/state/settings-store";
import type { ChatSession, Outcome } from "@/utils/ws/session";
import { loadConnectors, loadModels, loadSearch } from "@/utils/settings/actions";

/** Asks for a host list the first time it is shown, and again after a drop lost the request. */
function useLoaded<T>(loaded: Loaded<T>, load: (session: ChatSession) => Promise<Outcome>): Loaded<T> {
  const session = useSession();
  const open = useConnectionStore((s) => s.phase === "open");
  useEffect(() => {
    if (open && loaded.status === "idle") void load(session);
  }, [open, loaded.status, load, session]);
  return loaded;
}

export const useModels = () => useLoaded(useSettingsStore((s) => s.models), loadModels);
export const useSearchProviders = () => useLoaded(useSettingsStore((s) => s.search), loadSearch);
export const useConnectors = () => useLoaded(useSettingsStore((s) => s.connectors), loadConnectors);
