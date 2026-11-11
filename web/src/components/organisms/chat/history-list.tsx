"use client";

import { ThreadListItemPrimitive, ThreadListPrimitive, useAuiState } from "@assistant-ui/react";
import { createContext, useContext } from "react";

import { Badge } from "@/components/ui/badge";
import copy from "@/data/chat/rail.json";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";

type RowOptions = { disabled: boolean; testId: string; onChoose?: () => void };

const RowContext = createContext<RowOptions>({ disabled: false, testId: "history-row" });

/** One saved conversation; choosing it goes through the runtime to the address bar. */
function HistoryRow() {
  const { disabled, testId, onChoose } = useContext(RowContext);
  const id = useAuiState((s) => s.threadListItem.id);
  const title = useAuiState((s) => s.threadListItem.title);
  const interrupted = useAuiState((s) => s.threadListItem.custom?.interrupted === true);
  const current = useChatStore((s) => s.currentSessionId === id);
  // The host marks a conversation interrupted while its turn runs, in case it crashes; a live turn is not interrupted.
  const running = useChatStore((s) => s.activeId !== null);
  const showInterrupted = interrupted && !(current && running);
  const shown = title?.trim() || copy.untitled;
  return (
    <ThreadListItemPrimitive.Root className="group/row">
      <ThreadListItemPrimitive.Trigger
        disabled={disabled}
        onClick={onChoose}
        aria-current={current ? "page" : undefined}
        title={shown}
        data-testid={testId}
        className={cn(
          "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-sidebar-ring flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-start text-sm outline-none focus-visible:ring-2 disabled:cursor-default disabled:opacity-60 max-md:min-h-10",
          current && "bg-sidebar-accent text-sidebar-accent-foreground font-medium",
        )}
      >
        <span className="min-w-0 flex-1 truncate">{shown}</span>
        {showInterrupted ? (
          <Badge variant="outline" className="shrink-0" data-testid="interrupted-badge">
            {copy.interrupted}
          </Badge>
        ) : null}
      </ThreadListItemPrimitive.Trigger>
    </ThreadListItemPrimitive.Root>
  );
}

const components = { ThreadListItem: HistoryRow };

/**
 * The saved conversations `ids`, in that order, as compact rows. Rows come
 * from the runtime's own list, which can trail the store by a render, so an
 * id it does not know yet is left out until it does.
 */
export function HistoryList({
  ids,
  disabled,
  testId = "history-row",
  onChoose,
}: {
  ids: string[];
  disabled: boolean;
  testId?: string;
  /** Runs when a row is chosen, such as closing the sheet the rows sit in. */
  onChoose?: () => void;
}) {
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const indices = ids.map((id) => threadIds.indexOf(id)).filter((index) => index >= 0);
  return (
    <RowContext.Provider value={{ disabled, testId, onChoose }}>
      <ul className="flex min-h-0 flex-col gap-0.5">
        {indices.map((index) => (
          <li key={threadIds[index]}>
            <ThreadListPrimitive.ItemByIndex index={index} components={components} />
          </li>
        ))}
      </ul>
    </RowContext.Provider>
  );
}
