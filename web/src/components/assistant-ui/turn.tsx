"use client";

import { ActionBarPrimitive, useAuiState } from "@assistant-ui/react";
import { CopyIcon, RotateCcwIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import errorCopy from "@/data/chat/error-row.json";
import thinkingCopy from "@/data/chat/thinking.json";
import copy from "@/data/chat/transcript.json";
import { useCopy } from "@/hooks/use-copy";
import { useDensity } from "@/hooks/use-density";
import { useElapsed } from "@/hooks/use-elapsed";
import { useChatStore, type Turn } from "@/state/chat-store";
import { fill } from "@/utils/text";
import { turnIdOf } from "@/utils/thread-messages";

import { LiveDot } from "./work";

/** The turn behind the assistant message in scope. */
export function useMessageTurn(): Turn | undefined {
  const messageId = useAuiState((s) => s.message.id);
  return useChatStore((s) => s.turns.find((t) => t.id === turnIdOf(messageId)));
}

/**
 * Until the first words arrive: the host's status line, the running tool, and
 * the time spent. In Clean the work chip takes over once there is a tool call.
 */
export function ThinkingIndicator() {
  const turn = useMessageTurn();
  const [density] = useDensity();
  const running = useAuiState((s) => s.message.status?.type === "running");
  const hasText = useAuiState((s) => s.message.parts.some((p) => p.type === "text" && p.text !== ""));
  const hasTools = useAuiState((s) => s.message.parts.some((p) => p.type === "tool-call"));
  const seconds = useElapsed(turn?.startedAt ?? 0, turn?.endedAt ?? null, running);
  if (!running || hasText || turn === undefined || (density === "clean" && hasTools)) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={thinkingCopy.label}
      data-testid="thinking"
      className="text-muted-foreground flex min-w-0 items-center gap-2 text-sm"
    >
      <LiveDot />
      <span className="shimmer min-w-0 truncate">{turn.statusMessage || fill(thinkingCopy.working, { seconds })}</span>
      {turn.statusMessage ? (
        <span className="shrink-0 tabular-nums" data-elapsed="">
          {fill(thinkingCopy.working, { seconds })}
        </span>
      ) : null}
      {turn.activeTool ? <span className="text-foreground min-w-0 truncate font-mono text-xs">{turn.activeTool}</span> : null}
    </div>
  );
}

/** How a turn ended when it did not simply answer: stopped, cut at the step limit, or failed. */
export function TurnOutcome() {
  const status = useAuiState((s) => s.message.status);
  const hasText = useAuiState((s) => s.message.parts.some((p) => p.type === "text" && p.text !== ""));
  const turn = useMessageTurn();
  const { outcome, copy: copyText } = useCopy();
  if (status === undefined || status.type !== "incomplete") return null;
  if (status.reason === "cancelled" || status.reason === "length") {
    const text =
      status.reason === "cancelled"
        ? copy.stopped
        : turn?.maxSteps
          ? fill(copy.maxStepsCount, { count: turn.maxSteps })
          : copy.maxSteps;
    return (
      <div className="flex flex-wrap items-center gap-2" data-outcome={status.reason}>
        <p role="status" className="text-muted-foreground text-sm">
          {text}
        </p>
        {/* With text, the action bar below already offers Retry. */}
        {hasText ? null : (
          <ActionBarPrimitive.Reload
            render={<Button variant="outline" size="sm" data-testid="retry-turn" className="max-md:h-10" />}
          >
            <RotateCcwIcon aria-hidden />
            {errorCopy.retry}
          </ActionBarPrimitive.Reload>
        )}
      </div>
    );
  }
  if (status.reason !== "error") return null;
  const message = String(status.error ?? "");
  return (
    <Callout type="error" title={errorCopy.label} role="alert" data-testid="error-row" className="my-0">
      <p className="break-words">{message}</p>
      {turn?.errorCode ? (
        <p className="text-muted-foreground mt-1 font-mono text-xs" data-testid="error-code">
          {turn.errorCode}
        </p>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        <ActionBarPrimitive.Reload
          render={<Button variant="outline" size="sm" data-testid="retry-error" className="max-md:h-10" />}
        >
          <RotateCcwIcon aria-hidden />
          {errorCopy.retry}
        </ActionBarPrimitive.Reload>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => copyText(message)}
          data-testid="copy-error"
          className="max-md:h-10"
        >
          <CopyIcon aria-hidden />
          <span role="status" aria-live="polite">
            {outcome === "copied" ? errorCopy.copied : errorCopy.copyError}
          </span>
        </Button>
      </div>
    </Callout>
  );
}

/** Detail density only: tokens in and out and the time a finished turn took. */
export function TurnFooter() {
  const turn = useMessageTurn();
  const [density] = useDensity();
  if (density !== "detail" || !turn?.usage || turn.endedAt === null) return null;
  const seconds = Math.max(0, Math.round((turn.endedAt - turn.startedAt) / 1000));
  return (
    <p className="text-muted-foreground text-xs tabular-nums" data-testid="turn-footer" data-tokens="">
      {fill(copy.footer, {
        input: turn.usage.input_tokens.toLocaleString("en"),
        output: turn.usage.output_tokens.toLocaleString("en"),
        seconds,
      })}
    </p>
  );
}
