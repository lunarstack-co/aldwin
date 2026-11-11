"use client";

import { Plus, Settings } from "lucide-react";

import { Button } from "@/components/atoms/button";
import { Mark } from "@/components/atoms/mark";
import { ThemeToggle } from "@/components/molecules/theme-toggle";
import copy from "@/data/chat/rail.json";
import themeCopy from "@/data/theme-toggle.json";
import type { SessionMeta } from "@/types/protocol";
import { cn } from "@/utils/cn";

/**
 * The left rail: brand, new chat, the saved conversations, and the browser's
 * own settings. See all and Settings wait for their own screens.
 */
export function Rail({
  sessions,
  currentId,
  name,
  busy,
  onNewChat,
  newChatDisabled,
  onOpen,
}: {
  sessions: SessionMeta[];
  currentId: string | null;
  name: string;
  busy: boolean;
  onNewChat: () => void;
  newChatDisabled: boolean;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="flex h-full flex-col gap-4">
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
        <p className="eyebrow px-2">{copy.history}</p>
        {sessions.length === 0 ? (
          <p className="px-2 py-1.5 text-ui text-fg-tertiary">{copy.noHistory}</p>
        ) : (
          <ul className="flex min-h-0 flex-col gap-0.5 overflow-y-auto">
            {sessions.map((session) => {
              const current = session.id === currentId;
              return (
                <li key={session.id}>
                  <button
                    type="button"
                    onClick={() => onOpen(session.id)}
                    disabled={busy || current}
                    aria-current={current ? "page" : undefined}
                    data-testid="history-row"
                    className={cn(
                      "w-full cursor-pointer truncate rounded-control px-2 py-1.5 text-left text-ui text-fg-secondary hover:bg-neutral-muted hover:text-fg disabled:cursor-default disabled:opacity-100",
                      current && "bg-raised text-fg shadow-[0_1px_0_0_#1f23280a]",
                    )}
                  >
                    {session.title || copy.untitled}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <Button disabled data-testid="see-all" className="justify-start">
          {copy.seeAll}
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
