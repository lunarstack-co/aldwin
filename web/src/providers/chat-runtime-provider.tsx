"use client";

import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { useCallback, useMemo, type ReactNode } from "react";

import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { turnIdOf, turnsToMessages } from "@/utils/thread-messages";
import type { ChatSession } from "@/utils/ws/session";

const asIs = (message: ThreadMessageLike) => message;

function textOf(message: AppendMessage): string {
  return message.content
    .filter((part): part is { type: "text"; text: string } => part.type === "text")
    .map((part) => part.text)
    .join("\n");
}

/**
 * Bridges the chat store to the chat runtime. The runtime only sees messages;
 * sending, stopping and retrying go back through the session, so Retry adds
 * a new turn and the transcript grows.
 */
export function ChatRuntimeProvider({
  session,
  children,
}: {
  session: ChatSession;
  children: ReactNode;
}) {
  const turns = useChatStore((s) => s.turns);
  const activeId = useChatStore((s) => s.activeId);
  const phase = useConnectionStore((s) => s.phase);
  const messages = useMemo(() => turnsToMessages(turns), [turns]);

  const onNew = useCallback(
    async (message: AppendMessage) => {
      session.sendTurn(textOf(message));
    },
    [session],
  );
  const onCancel = useCallback(async () => session.stop(), [session]);
  const onReload = useCallback(
    async (parentId: string | null) => {
      const turnId = parentId === null ? null : turnIdOf(parentId);
      const turn = useChatStore.getState().turns.find((t) => t.id === turnId);
      if (turn?.userText) session.sendTurn(turn.userText);
      else session.retry();
    },
    [session],
  );

  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: asIs,
    isRunning: activeId !== null,
    isDisabled: phase !== "open",
    onNew,
    onCancel,
    onReload,
  });

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
