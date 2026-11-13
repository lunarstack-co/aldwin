"use client";

import { ActionBarPrimitive, AuiIf, MessagePrimitive, useAuiState } from "@assistant-ui/react";
import { CheckIcon, CopyIcon, RotateCcwIcon } from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect } from "react";

import { preloadDisplays } from "@/components/assistant-ui/displays";
import { MarkdownText } from "@/components/assistant-ui/elements/markdown-text";
import { ToolRow } from "@/components/assistant-ui/tool-row";
import { ThinkingIndicator, TurnFooter, TurnOutcome, useMessageTurn } from "@/components/assistant-ui/turn";
import { DetailToolGroup, WorkChip } from "@/components/assistant-ui/work";
import { Mark } from "@/components/brand/mark";
import { Button } from "@/components/ui/button";
import actions from "@/data/chat/message-actions.json";
import copy from "@/data/chat/transcript.json";
import { useDensity } from "@/hooks/use-density";
import { cn } from "@/utils/cn";
import { unattachedTurnApproval } from "@/utils/thread-messages";
import { formatClockTime } from "@/utils/time";

const ApprovalCardLazy = dynamic(
  () =>
    import("@/components/assistant-ui/approval-card").then((m) => m.ApprovalCardImpl),
  { ssr: false },
);

// The runtime's own placeholder for a running message with no words yet is a lone dot; the thinking line covers that wait.
const Nothing = () => null;

// Clean folds the turn's tool calls into one chip; WorkChip draws each call with ToolRow.
const cleanParts = { Text: MarkdownText, ChainOfThought: WorkChip, Empty: Nothing };
const detailParts = { Text: MarkdownText, tools: { Fallback: ToolRow }, ToolGroup: DetailToolGroup, Empty: Nothing };

/** Retry asks the last question again as a new turn. */
function Actions() {
  const isLast = useAuiState((s) => s.message.isLast);
  // Earlier answers keep the bar's space and only fade it in, so hovering never moves the transcript.
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="never"
      data-testid="message-actions"
      data-last={isLast || undefined}
      className={cn(
        "text-muted-foreground flex items-center gap-1 transition-opacity duration-150",
        // Touch screens cannot hover, so there the bar simply stays visible.
        !isLast && "[@media(hover:hover)]:opacity-0 group-hover/turn:opacity-100 focus-within:opacity-100",
      )}
    >
      <ActionBarPrimitive.Copy
        render={<Button variant="ghost" size="sm" data-testid="copy-answer" className="max-md:h-10" />}
      >
        <AuiIf condition={(s) => s.message.isCopied}>
          <CheckIcon aria-hidden />
        </AuiIf>
        <AuiIf condition={(s) => !s.message.isCopied}>
          <CopyIcon aria-hidden />
        </AuiIf>
        <span role="status" aria-live="polite">
          <AuiIf condition={(s) => s.message.isCopied}>{actions.copied}</AuiIf>
          <AuiIf condition={(s) => !s.message.isCopied}>{actions.copy}</AuiIf>
        </span>
      </ActionBarPrimitive.Copy>
      <ActionBarPrimitive.Reload
        render={<Button variant="ghost" size="sm" data-testid="retry-turn" className="max-md:h-10" />}
      >
        <RotateCcwIcon aria-hidden />
        {actions.retry}
      </ActionBarPrimitive.Reload>
    </ActionBarPrimitive.Root>
  );
}

export function AssistantMessage() {
  const [density] = useDensity();
  const turn = useMessageTurn();
  const status = useAuiState((s) => s.message.status?.type ?? "complete");
  const hasText = useAuiState((s) => s.message.parts.some((p) => p.type === "text" && p.text !== ""));
  const hasAnswer = !turn?.discarded && (hasText || Boolean(turn?.answer));
  const clockTime = formatClockTime(turn?.assistantAt);

  const hasWork = Boolean(turn && (turn.tools.length > 0 || turn.approval));
  useEffect(() => {
    if (!hasWork) return;
    preloadDisplays();
    void import("@/components/assistant-ui/approval-card");
  }, [hasWork]);

  const unattached = turn ? unattachedTurnApproval(turn) : null;
  // In clean density a card stands in for the folded work only while the turn runs; afterwards the chip holds it.
  const cleanApprovals =
    density === "clean" && turn && status === "running"
      ? [
          ...turn.tools
            .map((row) => ({
              approval: turn.approvalsByCallId[row.callId],
              resultDisplay: row.display,
            }))
            .filter((item): item is { approval: NonNullable<typeof item.approval>; resultDisplay: typeof item.resultDisplay } => Boolean(item.approval)),
          ...(unattached ? [{ approval: unattached, resultDisplay: null }] : []),
        ]
      : [];

  return (
    <MessagePrimitive.Root
      className="group/turn flex gap-3"
      aria-label={copy.answer}
      data-testid="turn"
      data-status={status}
    >
      {density === "detail" ? <Mark className="mt-0.5" /> : null}
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        {density === "detail" ? (
          <MessagePrimitive.Parts components={detailParts} />
        ) : (
          <MessagePrimitive.Parts components={cleanParts} />
        )}
        {density === "clean"
          ? cleanApprovals.map(({ approval, resultDisplay }) => (
              <ApprovalCardLazy
                key={approval.event.approval_id}
                approval={approval}
                resultDisplay={resultDisplay}
              />
            ))
          : unattached ? (
              <ApprovalCardLazy approval={unattached} />
            ) : null}
        <ThinkingIndicator />
        <TurnOutcome />
        <TurnFooter />
        <div className="flex items-center gap-2 empty:hidden">
          {status !== "running" && hasAnswer ? <Actions /> : null}
          {status !== "running" && clockTime && turn?.assistantAt && !turn.discarded ? (
            <time
              dateTime={turn.assistantAt}
              tabIndex={0}
              data-testid="message-time"
              className="text-muted-foreground ms-auto shrink-0 font-mono text-xs tabular-nums opacity-0 transition-opacity outline-none group-hover/turn:opacity-100 group-focus-within/turn:opacity-100 focus:opacity-100"
            >
              {clockTime}
            </time>
          ) : null}
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}
