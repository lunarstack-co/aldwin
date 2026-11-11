"use client";

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { Check, ChevronRight, LoaderCircle, X } from "lucide-react";
import { useState } from "react";

import copy from "@/data/chat/tool-card.json";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";
import type { ToolResult } from "@/utils/thread-messages";

function isToolResult(value: unknown): value is ToolResult {
  return typeof value === "object" && value !== null && "summary" in value;
}

/** One tool call: what ran, how it went, and the details on request. */
export const ToolCard: ToolCallMessagePartComponent = ({ toolName, argsText, result, isError }) => {
  const [open, setOpen] = useState(false);
  const finished = result !== undefined;
  const detail = isToolResult(result) ? result : null;
  const state = !finished ? "running" : isError ? "failed" : "ok";
  const Icon = state === "running" ? LoaderCircle : state === "failed" ? X : Check;
  const hasDetail = Boolean(argsText) || Boolean(detail?.summary);

  return (
    <div
      data-testid="tool-card"
      data-state={state}
      className="rounded-panel border border-border-subtle bg-raised text-ui"
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        disabled={!hasDetail}
        aria-expanded={hasDetail ? open : undefined}
        title={hasDetail ? (open ? copy.hide : copy.show) : undefined}
        className={cn(
          "flex w-full items-center gap-2 px-3 py-2 text-left",
          hasDetail && "cursor-pointer hover:bg-neutral-muted",
        )}
      >
        <Icon
          aria-hidden
          className={cn(
            "size-4 shrink-0",
            state === "running" && "animate-spin text-fg-secondary motion-reduce:animate-none",
            state === "ok" && "text-success",
            state === "failed" && "text-danger",
          )}
        />
        <span className="min-w-0 flex-1 truncate font-mono text-code text-fg">{toolName}</span>
        <span className="shrink-0 text-xs text-fg-secondary">
          {state === "running" ? copy.running : state === "ok" ? copy.ok : copy.failed}
        </span>
        {detail && detail.durationMs > 0 ? (
          <span className="shrink-0 text-xs text-fg-tertiary">
            {fill(copy.ms, { ms: detail.durationMs })}
          </span>
        ) : null}
        {hasDetail ? (
          <ChevronRight
            aria-hidden
            className={cn("size-3.5 shrink-0 text-fg-tertiary transition-transform duration-[80ms]", open && "rotate-90")}
          />
        ) : null}
      </button>
      {open ? (
        <dl className="flex flex-col gap-2 border-t border-border-subtle px-3 py-2">
          {argsText ? (
            <div>
              <dt className="eyebrow">{copy.arguments}</dt>
              <dd className="mt-1 whitespace-pre-wrap break-words font-mono text-code text-code-fg">
                {argsText}
              </dd>
            </div>
          ) : null}
          {detail?.summary ? (
            <div>
              <dt className="eyebrow">{copy.result}</dt>
              <dd className="mt-1 whitespace-pre-wrap break-words font-mono text-code text-code-fg">
                {detail.summary}
              </dd>
            </div>
          ) : null}
          {!argsText && !detail?.summary ? (
            <p className="text-xs text-fg-tertiary">{copy.noDetail}</p>
          ) : null}
        </dl>
      ) : null}
    </div>
  );
};
