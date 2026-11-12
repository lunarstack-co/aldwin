"use client";

import { useCallback, useEffect, type ReactNode } from "react";

import { ConnectionState } from "@/components/assistant-ui/connection-state";
import railCopy from "@/data/chat/rail.json";
import copy from "@/data/chat/shell.json";
import { useChatSession } from "@/hooks/use-chat-session";
import { useSkillsCatalog } from "@/hooks/use-skills-catalog";
import { useConversationRoute } from "@/hooks/use-conversation-route";
import { usePageTitle } from "@/hooks/use-page-title";
import { useSetup } from "@/hooks/use-setup";
import { ChatLayout } from "@/layouts/chat-layout";
import { ChatRuntimeProvider } from "@/providers/chat-runtime-provider";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { TITLE_CHARS } from "@/utils/page-title";
import { shorten } from "@/utils/text";

import { HistorySeeAll } from "./history-see-all";
import { Rail } from "./rail";
import { InterruptedNotice, SessionNoticeBanner } from "./session-notice-banner";
import { StoppedScreen } from "./stopped-screen";
import { TakeOverDialog } from "./take-over-dialog";
import { Thread } from "./thread";
import { ViewMenu } from "./view-menu";

/** Keeps the address bar and the tab title in step with the conversation. */
function RouteSync({ session }: { session: ReturnType<typeof useChatSession> }) {
  useConversationRoute(session);
  usePageTitle();
  return null;
}

function SkillsSync({ session }: { session: ReturnType<typeof useChatSession> }) {
  useSkillsCatalog(session);
  return null;
}

/** The chat screen: rail, stage, and the socket behind them. It lives across `/` and `/c/`. */
export function ChatShell({ children }: { children?: ReactNode }) {
  const { status } = useSetup();
  const session = useChatSession();
  const turns = useChatStore((s) => s.turns);
  const sessions = useChatStore((s) => s.sessions);
  const currentId = useChatStore((s) => s.currentSessionId);
  const view = useChatStore((s) => s.historyView);
  const setView = useChatStore((s) => s.setHistoryView);
  const phase = useConnectionStore((s) => s.phase);

  const open = phase === "open";
  const current = sessions.find((s) => s.id === currentId);
  const title =
    turns.length === 0 ? null : shorten(current?.title || turns[0].userText || railCopy.untitled, TITLE_CHARS);

  // A conversation saved from another window shows up when this one comes back into focus.
  useEffect(() => {
    function onFocus() {
      if (useConnectionStore.getState().phase === "open") session.refreshSessions();
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [session]);

  const showSeeAll = useCallback(() => {
    setView("see-all");
    session.refreshSessions();
  }, [session, setView]);
  const backToChat = useCallback(() => setView("chat"), [setView]);

  return (
    <ChatRuntimeProvider session={session}>
      <RouteSync session={session} />
      <SkillsSync session={session} />
      {children}
      <ChatLayout
        railLabel={copy.railLabel}
        stageLabel={copy.stageLabel}
        header={
          <>
            {title !== null && view === "chat" ? (
              <h1 className="me-auto min-w-0 truncate text-sm font-medium" data-testid="conversation-title">
                {title}
              </h1>
            ) : null}
            <ViewMenu />
          </>
        }
        rail={(close) => (
          <Rail
            sessions={sessions}
            currentId={currentId}
            disabled={!open}
            newChatDisabled={!open || (turns.length === 0 && view === "chat")}
            onSeeAll={() => {
              close();
              showSeeAll();
            }}
            onSearch={() => {
              close();
              showSeeAll();
            }}
            seeAllOpen={view === "see-all"}
            onNavigate={close}
          />
        )}
      >
        {phase === "stopped" ? (
          <StoppedScreen />
        ) : (
          <>
            <ConnectionState onRetry={session.reconnect} />
            <SessionNoticeBanner />
            {view === "chat" ? <InterruptedNotice /> : null}
            {view === "see-all" ? (
              <HistorySeeAll sessions={sessions} disabled={!open} onBack={backToChat} />
            ) : (
              <Thread model={status.model} disabled={!open} />
            )}
            {phase === "taken" ? <TakeOverDialog onTakeOver={session.takeOver} /> : null}
          </>
        )}
      </ChatLayout>
    </ChatRuntimeProvider>
  );
}

