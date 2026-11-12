"use client";

import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type AppendMessage,
  type ExternalStoreThreadData,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, type ReactNode } from "react";

import { leaveTo } from "@/components/organisms/leave-guard";
import { conversationHref } from "@/constants/routes";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { confirmLeave } from "@/state/unsaved-store";
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
 * Bridges the chat store to the chat runtime. The runtime only sees messages
 * and the saved conversations; sending, stopping, retrying and switching go
 * back through the session or the address bar, so Retry adds a new turn and
 * opening a conversation is a navigation like any other.
 */
export function ChatRuntimeProvider({
  session,
  children,
}: {
  session: ChatSession;
  children: ReactNode;
}) {
  const router = useRouter();
  const turns = useChatStore((s) => s.turns);
  const activeId = useChatStore((s) => s.activeId);
  const sessions = useChatStore((s) => s.sessions);
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
      if (turn?.userText) session.sendTurn(turn.userText, turn.pickedApps);
      else session.retry();
    },
    [session],
  );

  const threads = useMemo(
    (): ExternalStoreThreadData<"regular">[] =>
      sessions.map((s) => ({
        status: "regular",
        id: s.id,
        title: s.title || undefined,
        custom: { updatedAt: s.updated_at, interrupted: s.interrupted },
      })),
    [sessions],
  );
  const onSwitchToThread = useCallback(
    (id: string) => {
      if (id === useChatStore.getState().currentSessionId) useChatStore.getState().setHistoryView("chat");
      else confirmLeave(() => leaveTo(router, conversationHref(id)));
    },
    [router],
  );
  const onSwitchToNewThread = useCallback(() => {
    confirmLeave(() => {
      useChatStore.getState().setHistoryView("chat");
      leaveTo(router, "/");
    });
  }, [router]);

  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: asIs,
    isRunning: activeId !== null,
    isDisabled: phase !== "open",
    onNew,
    onCancel,
    onReload,
    adapters: {
      // No threadId: changing it makes the runtime rebuild its thread, and the
      // transcript already follows the open conversation through the store.
      threadList: {
        threads,
        onSwitchToThread,
        onSwitchToNewThread,
      },
    },
  });

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}
