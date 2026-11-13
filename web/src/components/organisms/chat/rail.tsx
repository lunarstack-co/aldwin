"use client";

import { ThreadListPrimitive } from "@assistant-ui/react";
import { Bug, ChevronRight, Search, Settings, SquarePen } from "lucide-react";
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

/** Recent rows: the newest unpinned ones, plus the open one when it is older. */
function recentIds(sessions: SessionMeta[], currentId: string | null): string[] {
  const ids = sessions.slice(0, RAIL_HISTORY_LIMIT).map((s) => s.id);
  const current = sessions.findIndex((s) => s.id === currentId);
  return current >= RAIL_HISTORY_LIMIT ? [...ids, sessions[current].id] : ids;
}

function SectionHeading({ children, testId }: { children: string; testId: string }) {
  return (
    <h2 className="text-sidebar-foreground/70 px-2 pb-1 text-xs font-medium" data-testid={testId}>
      {children}
    </h2>
  );
}

export function Rail({
  sessions,
  currentId,
  disabled,
  newChatDisabled,
  newChatCurrent,
  onSeeAll,
  onSearch,
  seeAllOpen,
  onNavigate,
}: {
  sessions: SessionMeta[];
  currentId: string | null;
  disabled: boolean;
  newChatDisabled: boolean;
  /** The page is already an empty new chat, so the button reads as the current page. */
  newChatCurrent: boolean;
  onSeeAll: () => void;
  onSearch: () => void;
  seeAllOpen: boolean;
  onNavigate?: () => void;
}) {
  // Until the host lists them, "no conversations" would be a guess.
  const listed = useChatStore((s) => s.sessionsLoaded);
  const onSettings = isSettingsPage(usePathname());
  const pinned = sessions.filter((s) => s.pinned);
  const recent = sessions.filter((s) => !s.pinned);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2">
        <Lockup onNavigate={onNavigate} className="ms-1" />
        <Button
          variant="ghost"
          size="icon"
          onClick={onSearch}
          disabled={disabled}
          aria-label={copy.search}
          title={copy.search}
          data-testid="rail-search"
          className="max-md:size-10"
        >
          <Search aria-hidden />
        </Button>
      </div>
      <ThreadListPrimitive.New
        render={
          <Button
            variant="ghost"
            disabled={newChatDisabled}
            aria-current={newChatCurrent ? "page" : undefined}
            data-testid="new-chat"
            className={cn(
              "text-sidebar-foreground mt-3 h-9 justify-start gap-2.5 px-2 font-medium max-md:h-10",
              newChatCurrent && "bg-sidebar-accent text-sidebar-accent-foreground disabled:opacity-100",
            )}
          />
        }
        onClick={onNavigate}
      >
        <SquarePen aria-hidden />
        {copy.newChat}
      </ThreadListPrimitive.New>
      <div className="mt-6 flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto">
        {pinned.length > 0 ? (
          <section className="flex flex-col" data-testid="pinned-section">
            <SectionHeading testId="pinned-eyebrow">{copy.pinned}</SectionHeading>
            <HistoryList ids={pinned.map((s) => s.id)} disabled={disabled} testId="pinned-row" onChoose={onNavigate} />
          </section>
        ) : null}
        <section className="flex flex-col">
          <SectionHeading testId="history-eyebrow">{copy.recents}</SectionHeading>
          {!listed ? null : sessions.length === 0 ? (
            <p className="text-sidebar-foreground/70 animate-in fade-in px-2 py-1.5 text-sm duration-300" data-testid="history-empty">
              {copy.noHistory}
            </p>
          ) : (
            <div className="animate-in fade-in duration-300">
              <HistoryList ids={recentIds(recent, currentId)} disabled={disabled} onChoose={onNavigate} />
            </div>
          )}
          {sessions.length === 0 ? null : (
            <Button
              variant="ghost"
              onClick={onSeeAll}
              disabled={disabled}
              aria-current={seeAllOpen ? "page" : undefined}
              data-testid="see-all"
              className="mt-0.5 justify-between px-2 max-md:h-10"
            >
              {copy.seeAll}
              <ChevronRight aria-hidden />
            </Button>
          )}
        </section>
      </div>
      <div className="mt-3 flex flex-col gap-1 border-t pt-3">
        <a
          href={REPORT_BUG_URL}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="report-bug"
          className={cn(buttonVariants({ variant: "ghost" }), "justify-start px-2 max-md:h-10")}
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
            "justify-start px-2 max-md:h-10",
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
