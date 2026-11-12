"use client";

import { useAuiState } from "@assistant-ui/react";
import { useEffect } from "react";

import { useConnectionStore } from "@/state/connection-store";
import { useSettingsStore } from "@/state/settings-store";
import { loadConnectors } from "@/utils/settings/actions";
import type { ChatSession } from "@/utils/ws/session";

// The `/` menu opens at the start of the text or after whitespace, like `@`.
const APP_START = /(?:^|\s)\//;

/** Asks the host for the connected apps the first time the composer holds a `/`. */
export function useAppsCatalog(session: ChatSession): void {
  const wanted = useAuiState((s) => APP_START.test(s.composer.text));
  const status = useSettingsStore((s) => s.connectors.status);
  const open = useConnectionStore((s) => s.phase === "open");

  useEffect(() => {
    if (wanted && open && status === "idle") void loadConnectors(session);
  }, [wanted, open, status, session]);
}
