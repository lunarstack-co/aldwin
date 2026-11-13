"use client";

import { ChainOfThoughtPrimitive, useAuiState } from "@assistant-ui/react";
import { ChevronDownIcon } from "lucide-react";
import type { ReactNode } from "react";

import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/assistant-ui/elements/tool-group.aui";
import copy from "@/data/chat/tool-group.json";
import { useElapsed } from "@/hooks/use-elapsed";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";
import { turnIdOf } from "@/utils/thread-messages";

import { ToolRow } from "./tool-row";

/** Blue only while something is running. */
export function LiveDot({ className }: { className?: string }) {
  return <span aria-hidden className={cn("bg-live animate-live-pulse inline-block size-1.5 rounded-full", className)} />;
}

/**
 * "Working for 8s" (or the host's status line), then "Worked for 12s", or a plain count for history
 * without timing. The turn's last group of calls carries its time; an earlier group just counts.
 */
function useWorkLabel(count: number, latest: boolean): { label: string; running: boolean } {
  const messageId = useAuiState((s) => s.message.id);
  const running = useAuiState((s) => s.message.status?.type === "running") && latest;
  const turn = useChatStore((s) => s.turns.find((t) => t.id === turnIdOf(messageId)));
  const seconds = useElapsed(turn?.startedAt ?? 0, turn?.endedAt ?? null, running);
  const timed = latest && turn !== undefined && turn.startedAt > 0 && (running || turn.endedAt !== null);
  const base = running
    ? turn?.statusMessage
      ? fill(copy.workingStatus, { status: turn.statusMessage, seconds })
      : fill(copy.working, { seconds })
    : timed
      ? fill(copy.done, { seconds })
      : fill(count === 1 ? copy.fromHistoryOne : copy.fromHistory, { count });
  return { label: base, running };
}

function FailedCount({ failed }: { failed: number }) {
  if (failed === 0) return null;
  return <span className="text-destructive">{fill(copy.failed, { count: failed })}</span>;
}

/** Clean mode: the turn's tool calls behind one line, collapsed once done. */
export function WorkChip() {
  const collapsed = useAuiState((s) => s.chainOfThought.collapsed);
  const count = useAuiState((s) => s.chainOfThought.parts.filter((p) => p.type === "tool-call").length);
  const failed = useAuiState(
    (s) => s.chainOfThought.parts.filter((p) => p.type === "tool-call" && p.isError === true).length,
  );
  // The last group holds the turn's last tool call.
  const latest = useAuiState((s) => {
    const last = s.message.parts.findLast((p) => p.type === "tool-call");
    return last?.type === "tool-call" && s.chainOfThought.parts.some((p) => p.type === "tool-call" && p.toolCallId === last.toolCallId);
  });
  const { label, running } = useWorkLabel(count, latest);

  return (
    <ChainOfThoughtPrimitive.Root
      data-testid="tool-group"
      data-open={collapsed ? undefined : ""}
      data-working={running || undefined}
      className="flex flex-col gap-1"
    >
      <ChainOfThoughtPrimitive.AccordionTrigger
        title={collapsed ? copy.show : copy.hide}
        className="text-muted-foreground hover:text-foreground flex w-fit cursor-pointer items-center gap-1.5 py-1 text-sm transition-colors max-md:min-h-10"
      >
        {running ? <LiveDot /> : null}
        <span className={cn(running && "shimmer")}>{label}</span>
        <FailedCount failed={failed} />
        <ChevronDownIcon aria-hidden className={cn("size-3.5 transition-transform", !collapsed && "rotate-180")} />
      </ChainOfThoughtPrimitive.AccordionTrigger>
      {collapsed ? null : (
        <div className="border-border ms-1.5 flex flex-col border-s ps-3">
          <ChainOfThoughtPrimitive.Parts components={{ tools: { Fallback: ToolRow } }} />
        </div>
      )}
    </ChainOfThoughtPrimitive.Root>
  );
}

/** Detail mode: consecutive tool calls shown open, under a count. */
export function DetailToolGroup({
  startIndex,
  endIndex,
  children,
}: {
  startIndex: number;
  endIndex: number;
  children?: ReactNode;
}) {
  const count = endIndex - startIndex + 1;
  const running = useAuiState(
    (s) => s.message.status?.type === "running" && !s.message.parts.slice(endIndex + 1).some((p) => p.type === "tool-call"),
  );
  return (
    <ToolGroupRoot variant="ghost" defaultOpen data-testid="tool-group">
      <ToolGroupTrigger count={count} active={running} />
      <ToolGroupContent>{children}</ToolGroupContent>
    </ToolGroupRoot>
  );
}
