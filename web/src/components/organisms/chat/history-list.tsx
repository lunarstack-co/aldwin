"use client";

import { ThreadListItemPrimitive, ThreadListPrimitive, useAuiState } from "@assistant-ui/react";
import { Ellipsis } from "lucide-react";
import { createContext, useContext } from "react";

import { Badge } from "@/components/ui/badge";
import copy from "@/data/chat/rail.json";
import { fill } from "@/utils/text";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";

import { ConversationMenu } from "./conversation-menu";

type RowOptions = {
  disabled: boolean;
  testId: string;
  onChoose?: () => void;
  /** `page` is See all, where titles read at full strength beside quieter date headings. */
  tone?: "rail" | "page";
};

const RowContext = createContext<RowOptions>({ disabled: false, testId: "history-row" });

/** Choosing a row goes through the runtime to the address bar. */
function HistoryRow() {
  const { disabled, testId, onChoose, tone = "rail" } = useContext(RowContext);
  const id = useAuiState((s) => s.threadListItem.id);
  const title = useAuiState((s) => s.threadListItem.title);
  const interrupted = useAuiState((s) => s.threadListItem.custom?.interrupted === true);
  const current = useChatStore((s) => s.currentSessionId === id);
  // The host marks a conversation interrupted while its turn runs, in case it crashes; a live turn is not interrupted.
  const running = useChatStore((s) => s.activeId !== null);
  const showInterrupted = interrupted && !(current && running);
  const shown = title?.trim() || copy.untitled;
  return (
    <ThreadListItemPrimitive.Root className="group/row relative">
      <ThreadListItemPrimitive.Trigger
        disabled={disabled}
        onClick={onChoose}
        aria-current={current ? "page" : undefined}
        title={shown}
        data-testid={testId}
        className={cn(
          tone === "page" ? "text-foreground" : "text-sidebar-foreground/70",
          "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:ring-sidebar-ring flex w-full cursor-pointer items-center gap-2 rounded-md py-1.5 ps-2 pe-9 text-start text-sm outline-none focus-visible:ring-2 disabled:cursor-default disabled:opacity-60 max-md:min-h-10",
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
      <ConversationMenu
        target={{ id, title: shown }}
        side="right"
        trigger={
          <button
            type="button"
            disabled={disabled}
            aria-label={fill(copy.options, { title: shown })}
            data-testid="conversation-options"
            // Shown on hover or focus, and always on touch screens, which cannot hover.
            className="text-sidebar-foreground/70 hover:text-sidebar-foreground focus-visible:ring-sidebar-ring data-popup-open:opacity-100 absolute end-1 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-md opacity-0 outline-none group-focus-within/row:opacity-100 group-hover/row:opacity-100 focus-visible:ring-2 disabled:hidden max-md:size-9 [@media(hover:none)]:opacity-100 [&_svg]:size-4"
          >
            <Ellipsis aria-hidden />
          </button>
        }
      />
    </ThreadListItemPrimitive.Root>
  );
}

const components = { ThreadListItem: HistoryRow };

/** The runtime's list can trail the store by a render, so unknown ids are skipped until they appear. */
export function HistoryList({
  ids,
  disabled,
  testId = "history-row",
  onChoose,
  tone,
}: {
  ids: string[];
  disabled: boolean;
  testId?: string;
  /** Runs when a row is chosen, such as closing the sheet the rows sit in. */
  onChoose?: () => void;
  tone?: "rail" | "page";
}) {
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const indices = ids.map((id) => threadIds.indexOf(id)).filter((index) => index >= 0);
  return (
    <RowContext.Provider value={{ disabled, testId, onChoose, tone }}>
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
