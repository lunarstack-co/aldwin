"use client";

import { ThreadListPrimitive } from "@assistant-ui/react";
import { Bug, ChevronRight, Plus, Search, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { Lockup } from "@/components/brand/lockup";
import { Button, buttonVariants } from "@/components/ui/button";
import { RAIL_HISTORY_LIMIT } from "@/constants/chat";
import { REPORT_BUG_URL, ROUTES, isSettingsPage } from "@/constants/routes";
import copy from "@/data/chat/rail.json";
import { useChatStore } from "@/state/chat-store";
import type { SessionMeta } from "@/types/protocol";
import { cn } from "@/utils/cn";

import { HistoryList } from "./history-list";

/** Rows the rail shows: the most recent, plus the open one when it is older. */
function railIds(sessions: SessionMeta[], currentId: string | null): string[] {
  const ids = sessions.slice(0, RAIL_HISTORY_LIMIT).map((s) => s.id);
  const current = sessions.findIndex((s) => s.id === currentId);
  return current >= RAIL_HISTORY_LIMIT ? [...ids, sessions[current].id] : ids;
}

export function Rail({
  sessions,
  currentId,
  disabled,
  newChatDisabled,
  onSeeAll,
  onSearch,
  seeAllOpen,
  onNavigate,
}: {
  sessions: SessionMeta[];
  currentId: string | null;
  disabled: boolean;
  newChatDisabled: boolean;
  onSeeAll: () => void;
  onSearch: () => void;
  seeAllOpen: boolean;
  onNavigate?: () => void;
}) {
  // Until the host lists them, "no conversations" would be a guess.
  const listed = useChatStore((s) => s.sessionsLoaded);
  const onSettings = isSettingsPage(usePathname());
  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <Lockup onNavigate={onNavigate} className="self-start" />
      <div className="flex flex-col gap-0.5">
        <ThreadListPrimitive.New
          render={
            <Button
              variant="ghost"
              disabled={newChatDisabled}
              data-testid="new-chat"
              className="justify-start max-md:h-10"
            />
          }
          onClick={onNavigate}
        >
          <Plus aria-hidden />
          {copy.newChat}
        </ThreadListPrimitive.New>
        <Button
          variant="ghost"
          onClick={onSearch}
          disabled={disabled}
          data-testid="rail-search"
          className="justify-start max-md:h-10"
        >
          <Search aria-hidden />
          {copy.search}
        </Button>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-1">
        <h2 className="text-sidebar-foreground/70 px-2 text-xs font-medium" data-testid="history-eyebrow">
          {copy.history}
        </h2>
        {!listed ? null : sessions.length === 0 ? (
          <p className="text-sidebar-foreground/70 animate-in fade-in px-2 py-1.5 text-sm duration-300" data-testid="history-empty">
            {copy.noHistory}
          </p>
        ) : (
          <div className="animate-in fade-in min-h-0 overflow-y-auto duration-300">
            <HistoryList ids={railIds(sessions, currentId)} disabled={disabled} onChoose={onNavigate} />
          </div>
        )}
        {sessions.length === 0 ? null : (
          <Button
            variant="ghost"
            onClick={onSeeAll}
            disabled={disabled}
            aria-current={seeAllOpen ? "page" : undefined}
            data-testid="see-all"
            className="justify-between max-md:h-10"
          >
            {copy.seeAll}
            <ChevronRight aria-hidden />
          </Button>
        )}
      </div>
      <div className="flex flex-col gap-1 border-t pt-3">
        <a
          href={REPORT_BUG_URL}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="report-bug"
          className={cn(buttonVariants({ variant: "ghost" }), "justify-start max-md:h-10")}
        >
          <Bug aria-hidden />
          {copy.reportBug}
          <span className="sr-only"> {copy.opensNewTab}</span>
        </a>
        <Link
          href={ROUTES.settings}
          onClick={onNavigate}
          aria-current={onSettings ? "page" : undefined}
          data-testid="settings"
          className={cn(
            buttonVariants({ variant: "ghost" }),
            "justify-start max-md:h-10",
            onSettings && "bg-sidebar-accent text-sidebar-accent-foreground",
          )}
        >
          <Settings aria-hidden />
          {copy.settings}
        </Link>
      </div>
    </div>
  );
}
