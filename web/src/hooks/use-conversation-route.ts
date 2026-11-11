"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";

import { ROUTES, conversationHref, isConversationPage } from "@/constants/routes";
import copy from "@/data/chat/route.json";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { isConversationId } from "@/utils/conversation-id";
import type { ChatSession } from "@/utils/ws/session";

/** Where the address bar should be when the host has `currentId` open. */
function hrefFor(currentId: string | null, hasTurns: boolean): string {
  return currentId !== null && hasTurns ? conversationHref(currentId) : ROUTES.home;
}

/**
 * Keeps the address bar and the open conversation in step. The URL leads:
 * the rail and See all only navigate, and this hook asks the host to open
 * what the URL names. The host answers back through the store, and the URL
 * follows when a new chat gets its id.
 */
export function useConversationRoute(session: ChatSession): void {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const connected = useConnectionStore((s) => s.phase === "open");
  const listed = useChatStore((s) => s.sessionsLoaded);
  // Until the host has said which conversation is open, the URL cannot be compared with it.
  const open = connected && listed;
  const currentId = useChatStore((s) => s.currentSessionId);
  const hasTurns = useChatStore((s) => s.turns.length > 0);

  const onConversation = isConversationPage(pathname);
  const routeId = onConversation ? params.get("id") : null;

  // The id this tab last asked the host to open; a later navigation replaces it.
  const requested = useRef<string | null>(null);
  // The id the host just refused; not asked again while the address still names it.
  const refused = useRef<string | null>(null);
  // True while a move to `/` waits for the host to start the new chat.
  const leaving = useRef(false);
  const lastPath = useRef<string | null>(null);

  // URL to host: open what the address names.
  useEffect(() => {
    if (!open) {
      // A drop loses the request in flight; ask again once the socket is back.
      requested.current = null;
      return;
    }
    if (!onConversation) return;
    if (!isConversationId(routeId)) {
      useChatStore.getState().pushToast(copy.badConversation);
      router.replace(ROUTES.home);
      return;
    }
    if (refused.current !== routeId) refused.current = null;
    if (routeId === currentId) {
      requested.current = null;
      return;
    }
    if (requested.current === routeId || refused.current === routeId) return;
    requested.current = routeId;
    session.openConversation(routeId, (opened) => {
      if (requested.current !== routeId) return;
      requested.current = null;
      if (!opened) {
        refused.current = routeId;
        const state = useChatStore.getState();
        router.replace(hrefFor(state.currentSessionId, state.turns.length > 0));
      }
    });
  }, [open, onConversation, routeId, currentId, session, router]);

  // Arriving at `/` from a conversation means a new chat.
  useEffect(() => {
    const previous = lastPath.current;
    lastPath.current = pathname;
    if (!open || onConversation || previous === null || previous === pathname) return;
    if (!useChatStore.getState().turns.length) return;
    leaving.current = true;
    session.newChat((started) => {
      if (started) return;
      leaving.current = false;
      const state = useChatStore.getState();
      router.replace(hrefFor(state.currentSessionId, state.turns.length > 0));
    });
  }, [pathname, open, onConversation, session, router]);

  // Host to URL: once a conversation on `/` has turns and an id, give it its address.
  useEffect(() => {
    if (!open) return;
    if (leaving.current) {
      if (!hasTurns) leaving.current = false;
      return;
    }
    if (!onConversation && hasTurns && currentId !== null) {
      router.replace(conversationHref(currentId));
    }
  }, [open, onConversation, hasTurns, currentId, router]);
}
