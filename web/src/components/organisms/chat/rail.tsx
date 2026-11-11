"use client";

import { ChevronRight, Plus, Settings } from "lucide-react";

import { Button } from "@/components/atoms/button";
import { Mark } from "@/components/atoms/mark";
import { ThemeToggle } from "@/components/molecules/theme-toggle";
import { RAIL_HISTORY_LIMIT } from "@/constants/chat";
import copy from "@/data/chat/rail.json";
import themeCopy from "@/data/theme-toggle.json";
import type { SessionMeta } from "@/types/protocol";

import { HistoryRailList } from "./history-rail-list";

/**
 * The left rail: brand, new chat, the most recent conversations and a way
 * to see all of them, and the browser's own settings. Settings waits for its
 * own screen.
 */
export function Rail({
  sessions,
  currentId,
  name,
  disabled,
  onNewChat,
  newChatDisabled,
  onOpen,
  onSeeAll,
  seeAllOpen,
}: {
  sessions: SessionMeta[];
  currentId: string | null;
  name: string;
  disabled: boolean;
  onNewChat: () => void;
  newChatDisabled: boolean;
  onOpen: (id: string) => void;
  onSeeAll: () => void;
  seeAllOpen: boolean;
}) {
  // Opening an old conversation does not bump it, so the open one stays in the rail either way.
  const recent = sessions.slice(0, RAIL_HISTORY_LIMIT);
  const current = sessions.find((s) => s.id === currentId);
  const shown = current && !recent.includes(current) ? [...recent, current] : recent;
  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <div className="flex items-center gap-2 px-1">
        <Mark />
        <span className="text-ui font-semibold">{copy.brand}</span>
      </div>
      <Button
        variant="outline"
        onClick={onNewChat}
        disabled={newChatDisabled}
        data-testid="new-chat"
        className="justify-start"
      >
        <Plus aria-hidden className="size-4" />
        {copy.newChat}
      </Button>
      <div className="flex min-h-0 flex-col gap-1">
        <p className="eyebrow px-2 uppercase tracking-wide" data-testid="history-eyebrow">
          {copy.history}
        </p>
        {sessions.length === 0 ? (
          <p className="px-2 py-1.5 text-ui text-fg-tertiary">{copy.noHistory}</p>
        ) : (
          <div className="min-h-0 overflow-y-auto">
            <HistoryRailList
              sessions={shown}
              currentId={currentId}
              disabled={disabled}
              onOpen={onOpen}
              untitled={copy.untitled}
            />
          </div>
        )}
        <Button
          onClick={onSeeAll}
          disabled={disabled}
          aria-current={seeAllOpen ? "page" : undefined}
          data-testid="see-all"
          className="justify-between"
        >
          {copy.seeAll}
          <ChevronRight aria-hidden className="size-4" />
        </Button>
      </div>
      <div className="mt-auto flex flex-col gap-3 border-t border-border-subtle pt-3">
        <Button disabled data-testid="settings" className="justify-start">
          <Settings aria-hidden className="size-4" />
          {copy.settings}
        </Button>
        <ThemeToggle labels={themeCopy} />
        {name ? <p className="px-1 text-xs text-fg-tertiary">{name}</p> : null}
      </div>
    </div>
  );
}
