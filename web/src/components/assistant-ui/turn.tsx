"use client";

import { ActionBarPrimitive, useAuiState } from "@assistant-ui/react";
import { CopyIcon, RotateCcwIcon } from "lucide-react";
import dynamic from "next/dynamic";

import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import errorCopy from "@/data/chat/error-row.json";
import thinkingCopy from "@/data/chat/thinking.json";
import copy from "@/data/chat/transcript.json";
import { useCopy } from "@/hooks/use-copy";
import { useDensity } from "@/hooks/use-density";
import { useElapsed } from "@/hooks/use-elapsed";
import { useSession } from "@/providers/chat-session-provider";
import { useChatStore, type Turn } from "@/state/chat-store";
import { fill } from "@/utils/text";
import { turnIdOf } from "@/utils/thread-messages";
import { computeMessageTiming } from "@/utils/time";

import { LiveDot } from "./work";

const StoppedRunLazy = dynamic(
  () => import("./elements/stopped-run").then((m) => m.StoppedRun),
  { ssr: false },
);
const MessageTimingLazy = dynamic(
  () => import("./elements/message-timing").then((m) => m.MessageTiming),
  { ssr: false },
);

export function useMessageTurn(): Turn | undefined {
  const messageId = useAuiState((s) => s.message.id);
  return useChatStore((s) => s.turns.find((t) => t.id === turnIdOf(messageId)));
}

/** Shown until the first words arrive; in Clean the work chip takes over once a tool runs. */
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

export function TurnOutcome() {
  const status = useAuiState((s) => s.message.status);
  const hasText = useAuiState((s) => s.message.parts.some((p) => p.type === "text" && p.text !== ""));
  const turn = useMessageTurn();
  const session = useSession();
  const { outcome, copy: copyText } = useCopy();
  if (status === undefined || status.type !== "incomplete") return null;
  if (turn?.discarded) return null;

  if (status.reason === "cancelled") {
    const partial = turn?.answer ?? "";
    return (
      <div className="flex flex-col gap-2" data-outcome="cancelled">
        <StoppedRunLazy
          words={[]}
          reason={copy.stopped}
          continueLabel={copy.continue}
          discardLabel={copy.discard}
          onContinue={() => session.sendTurn(copy.continue)}
          onDiscard={
            partial && turn
              ? () => useChatStore.getState().discardTurn(turn.id)
              : undefined
          }
        />
        {partial ? null : (
          <div>
            <ActionBarPrimitive.Reload
              render={<Button variant="outline" size="sm" data-testid="retry-turn" className="max-md:h-10" />}
            >
              <RotateCcwIcon aria-hidden />
              {errorCopy.retry}
            </ActionBarPrimitive.Reload>
          </div>
        )}
      </div>
    );
  }

  if (status.reason === "length") {
    const text = turn?.maxSteps ? fill(copy.maxStepsCount, { count: turn.maxSteps }) : copy.maxSteps;
    return (
      <div className="flex flex-wrap items-center gap-2" data-outcome="length">
        <p role="status" className="text-muted-foreground text-sm">
          {text}
        </p>
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

/** Detail density only. */
export function TurnFooter() {
  const turn = useMessageTurn();
  const [density] = useDensity();
  if (density !== "detail" || !turn?.usage || turn.endedAt === null || turn.discarded) return null;
  const seconds = Math.max(0, Math.round((turn.endedAt - turn.startedAt) / 1000));
  const timing = computeMessageTiming(
    turn.startedAt,
    turn.firstTokenAt,
    turn.endedAt,
    turn.usage.output_tokens,
  );

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <p className="text-muted-foreground text-xs tabular-nums" data-testid="turn-footer" data-tokens="">
        {fill(copy.footer, {
          input: turn.usage.input_tokens.toLocaleString("en"),
          output: turn.usage.output_tokens.toLocaleString("en"),
          seconds,
        })}
      </p>
      {timing ? (
        <MessageTimingLazy
          data-testid="message-timing"
          className="w-auto max-w-none"
          stats={[
            { label: copy.ttft, value: timing.firstTokenSeconds },
            { label: copy.speed, value: timing.tokensPerSecond },
          ]}
        />
      ) : null}
    </div>
  );
}
