"use client";

import type { SessionMeta } from "@/types/protocol";
import { cn } from "@/utils/cn";
import { displayTitle } from "@/utils/history";

/** Saved conversations as compact rows, the open one marked as the current page. */
export function HistoryRailList({
  sessions,
  currentId,
  disabled,
  onOpen,
  untitled,
  testId = "history-row",
}: {
  sessions: SessionMeta[];
  currentId: string | null;
  disabled: boolean;
  onOpen: (id: string) => void;
  untitled: string;
  testId?: string;
}) {
  return (
    <ul className="flex min-h-0 flex-col gap-0.5">
      {sessions.map((session) => {
        const current = session.id === currentId;
        const title = displayTitle(session, untitled);
        return (
          <li key={session.id}>
            <button
              type="button"
              onClick={() => onOpen(session.id)}
              disabled={disabled}
              aria-current={current ? "page" : undefined}
              title={title}
              data-testid={testId}
              className={cn(
                "w-full cursor-pointer truncate rounded-control px-2 py-1.5 text-left text-ui text-fg-secondary hover:bg-neutral-muted hover:text-fg disabled:cursor-default disabled:opacity-60",
                current && "bg-raised text-fg shadow-[0_1px_0_0_#1f23280a]",
              )}
            >
              {title}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
