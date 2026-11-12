"use client";

import { ActionBarPrimitive, AuiIf, MessagePrimitive, useAuiState } from "@assistant-ui/react";
import { CheckIcon, CopyIcon, RotateCcwIcon } from "lucide-react";

import { MarkdownText } from "@/components/assistant-ui/elements/markdown-text";
import { ToolRow } from "@/components/assistant-ui/tool-row";
import { ThinkingIndicator, TurnFooter, TurnOutcome } from "@/components/assistant-ui/turn";
import { DetailToolGroup, WorkChip } from "@/components/assistant-ui/work";
import { Mark } from "@/components/brand/mark";
import { Button } from "@/components/ui/button";
import actions from "@/data/chat/message-actions.json";
import copy from "@/data/chat/transcript.json";
import { useDensity } from "@/hooks/use-density";

// The runtime's own placeholder for a running message with no words yet is a lone dot; the thinking line covers that wait.
const Nothing = () => null;

// Clean folds the turn's tool calls into one chip; WorkChip draws each call with ToolRow.
const cleanParts = { Text: MarkdownText, ChainOfThought: WorkChip, Empty: Nothing };
const detailParts = { Text: MarkdownText, tools: { Fallback: ToolRow }, ToolGroup: DetailToolGroup, Empty: Nothing };

/** Copy and Retry, and nothing else; Retry asks the last question again as a new turn. */
function Actions() {
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="not-last"
      data-testid="message-actions"
      className="text-muted-foreground flex items-center gap-1"
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

/** The answer to one question: the work behind it, the text, how it ended, and what to do next. */
export function AssistantMessage() {
  const [density] = useDensity();
  const status = useAuiState((s) => s.message.status?.type ?? "complete");
  const hasText = useAuiState((s) => s.message.parts.some((p) => p.type === "text" && p.text !== ""));
  return (
    <MessagePrimitive.Root
      className="flex gap-3"
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
        <ThinkingIndicator />
        <TurnOutcome />
        <TurnFooter />
        {status !== "running" && hasText ? <Actions /> : null}
      </div>
    </MessagePrimitive.Root>
  );
}
