"use client";

import { useAuiState } from "@assistant-ui/react";
import { useEffect } from "react";

import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import type { ChatSession } from "@/utils/ws/session";

// The same start rule the `@` menu uses: at the start or after whitespace.
const MENTION_START = /(?:^|\s)@/;

/** Asks the host for its skills the first time the composer holds an `@`, and again after Try again. */
export function useSkillsCatalog(session: ChatSession): void {
  const wanted = useAuiState((s) => MENTION_START.test(s.composer.text));
  const status = useChatStore((s) => s.skills.status);
  const open = useConnectionStore((s) => s.phase === "open");

  useEffect(() => {
    if (wanted && open && status === "idle") session.listSkills();
  }, [wanted, open, status, session]);
}
