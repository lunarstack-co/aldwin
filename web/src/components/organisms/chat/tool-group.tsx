"use client";

import { useAuiState } from "@assistant-ui/react";
import { ChevronRight, LoaderCircle } from "lucide-react";
import { useState, type ReactNode } from "react";

import copy from "@/data/chat/tool-group.json";
import { useDensity } from "@/hooks/use-density";
import { useElapsed } from "@/hooks/use-elapsed";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";
import { turnIdOf } from "@/utils/thread-messages";

/**
 * The tool calls of one answer behind a single line. Clean mode keeps them
 * folded; Detail opens them. Either way the line shows how long the work took.
 */
export function ToolGroup({
  startIndex,
  endIndex,
  children,
}: {
  startIndex: number;
  endIndex: number;
  children?: ReactNode;
}) {
  const messageId = useAuiState((s) => s.message.id);
  const running = useAuiState((s) => s.message.status?.type === "running");
  const turn = useChatStore((s) => s.turns.find((t) => t.id === turnIdOf(messageId)));
  const [density] = useDensity();
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? density === "detail";
  const seconds = useElapsed(turn?.startedAt ?? 0, turn?.endedAt ?? null, running);
  const count = endIndex - startIndex + 1;
  const timed = turn !== undefined && (running || turn.endedAt !== null);
  const label = running
    ? fill(copy.working, { seconds })
    : timed
      ? fill(count === 1 ? copy.doneOne : copy.done, { count, seconds })
      : fill(count === 1 ? copy.fromHistoryOne : copy.fromHistory, { count });

  return (
    <div className="flex flex-col gap-2" data-testid="tool-group" data-open={open} data-working={running || undefined}>
      <button
        type="button"
        onClick={() => setOverride(!open)}
        aria-expanded={open}
        title={open ? copy.hide : copy.show}
        className={cn(
          "inline-flex h-7 w-fit cursor-pointer items-center gap-1.5 rounded-full border border-border-default px-2.5 text-xs font-medium text-fg-secondary hover:bg-neutral-muted hover:text-fg",
          running && "work-chip-live",
        )}
      >
        {running ? (
          <LoaderCircle aria-hidden className="size-3.5 animate-spin motion-reduce:animate-none" />
        ) : null}
        <span>{label}</span>
        <ChevronRight
          aria-hidden
          className={cn("size-3.5 transition-transform duration-[80ms]", open && "rotate-90")}
        />
      </button>
      {open ? <div className="flex flex-col gap-1.5">{children}</div> : null}
    </div>
  );
}
