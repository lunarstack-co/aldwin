"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { isConversationPage, isHomePage } from "@/constants/routes";
import appCopy from "@/data/app.json";
import { useChatStore } from "@/state/chat-store";
import { pageTitle } from "@/utils/page-title";

/**
 * The tab title on a conversation page: its name, with a dot while Aldwin
 * answers. The static export cannot know the name, so it is set here.
 */
export function usePageTitle(): void {
  const pathname = usePathname();
  const onConversation = isConversationPage(pathname);
  // Pages other than the chat keep the static title their metadata gives them.
  const onChat = onConversation || isHomePage(pathname);
  const currentId = useChatStore((s) => s.currentSessionId);
  const title = useChatStore((s) => s.sessions.find((x) => x.id === s.currentSessionId)?.title ?? "");
  const firstUserText = useChatStore((s) => s.turns.find((t) => t.userText)?.userText ?? "");
  const running = useChatStore((s) => s.activeId !== null);
  const hasTurns = useChatStore((s) => s.turns.length > 0);

  useEffect(() => {
    // A new chat keeps its static title until its first message names it.
    if (!onChat || (!onConversation && !hasTurns)) return;
    const wanted = pageTitle({
      title,
      firstUserText,
      running,
      fallback: appCopy.pages.conversation.title,
      app: appCopy.title,
    });
    document.title = wanted;
    // Next rewrites the head title on client navigation, after this effect has run.
    const observer = new MutationObserver(() => {
      if (document.title !== wanted) document.title = wanted;
    });
    observer.observe(document.head, { subtree: true, childList: true, characterData: true });
    return () => observer.disconnect();
  }, [onChat, onConversation, hasTurns, currentId, title, firstUserText, running, pathname]);
}
