"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef } from "react";

import { ROUTES, chatHref, conversationHref, isConversationPage, isHomePage } from "@/constants/routes";
import copy from "@/data/chat/route.json";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { isConversationId } from "@/utils/conversation-id";
import type { ChatSession } from "@/utils/ws/session";

/** The URL leads: the rail and See all only navigate, and the URL follows once a new chat gets its id. */
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
  // Other pages, such as Settings, leave the open conversation alone.
  const onHome = isHomePage(pathname);
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
        router.replace(chatHref(state.currentSessionId, state.turns.length > 0));
      }
    });
  }, [open, onConversation, routeId, currentId, session, router]);

  // Arriving at `/` from a conversation means a new chat.
  useEffect(() => {
    const previous = lastPath.current;
    lastPath.current = pathname;
    if (!open || !onHome || previous === null || previous === pathname) return;
    if (!useChatStore.getState().turns.length) return;
    leaving.current = true;
    session.newChat((started) => {
      if (started) return;
      leaving.current = false;
      const state = useChatStore.getState();
      router.replace(chatHref(state.currentSessionId, state.turns.length > 0));
    });
  }, [pathname, open, onHome, session, router]);

  // Host to URL: once a conversation on `/` has turns and an id, give it its address.
  useEffect(() => {
    if (!open) return;
    if (leaving.current) {
      if (!hasTurns) leaving.current = false;
      return;
    }
    if (onHome && hasTurns && currentId !== null) {
      router.replace(conversationHref(currentId));
    }
  }, [open, onHome, hasTurns, currentId, router]);
}
