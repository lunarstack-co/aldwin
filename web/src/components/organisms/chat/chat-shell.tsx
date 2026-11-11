"use client";

import { useCallback, useEffect } from "react";

import { DensityToggle } from "@/components/molecules/density-toggle";
import densityCopy from "@/data/chat/density-toggle.json";
import railCopy from "@/data/chat/rail.json";
import copy from "@/data/chat/shell.json";
import { useChatSession } from "@/hooks/use-chat-session";
import { useDensity } from "@/hooks/use-density";
import { useSetup } from "@/hooks/use-setup";
import { ChatLayout } from "@/layouts/chat-layout";
import { ChatRuntimeProvider } from "@/providers/chat-runtime-provider";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { shorten } from "@/utils/text";

import { HistorySeeAll } from "./history-see-all";
import { Rail } from "./rail";
import { ReconnectBanner } from "./reconnect-banner";
import { SessionNoticeBanner } from "./session-notice-banner";
import { StoppedScreen } from "./stopped-screen";
import { TakeOverDialog } from "./take-over-dialog";
import { Thread } from "./thread";

const TITLE_CHARS = 60;

/** The chat screen: rail, stage, and the socket behind them. */
export function ChatShell() {
  const { status } = useSetup();
  const session = useChatSession();
  const turns = useChatStore((s) => s.turns);
  const sessions = useChatStore((s) => s.sessions);
  const currentId = useChatStore((s) => s.currentSessionId);
  const view = useChatStore((s) => s.historyView);
  const setView = useChatStore((s) => s.setHistoryView);
  const phase = useConnectionStore((s) => s.phase);
  const [density, setDensity] = useDensity();

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
      <ChatLayout
        railLabel={copy.railLabel}
        stageLabel={copy.stageLabel}
        rail={
          <Rail
            sessions={sessions}
            currentId={currentId}
            name={status.profile.first_name}
            disabled={!open}
            onNewChat={session.newChat}
            newChatDisabled={!open || (turns.length === 0 && view === "chat")}
            onOpen={session.openConversation}
            onSeeAll={showSeeAll}
            seeAllOpen={view === "see-all"}
          />
        }
      >
        {phase === "stopped" ? (
          <StoppedScreen />
        ) : (
          <>
            <ReconnectBanner onRetry={session.reconnect} />
            <SessionNoticeBanner />
            {view === "see-all" ? (
              <HistorySeeAll
                sessions={sessions}
                currentId={currentId}
                disabled={!open}
                onOpen={session.openConversation}
                onBack={backToChat}
              />
            ) : (
              <>
                {title !== null ? (
                  <header className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-2 sm:px-6">
                    <h1 className="truncate text-ui font-medium">{title}</h1>
                    <DensityToggle value={density} onChange={setDensity} labels={densityCopy} />
                  </header>
                ) : null}
                <Thread model={status.model} disabled={!open} />
              </>
            )}
            {phase === "taken" ? <TakeOverDialog onTakeOver={session.takeOver} /> : null}
          </>
        )}
      </ChatLayout>
    </ChatRuntimeProvider>
  );
}
