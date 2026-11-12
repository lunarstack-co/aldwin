"use client";

import type { ToolCallMessagePartComponent } from "@assistant-ui/react";
import { CheckIcon, ChevronDownIcon, LoaderIcon, XCircleIcon } from "lucide-react";

import { CollapsibleContent, CollapsibleTrigger } from "@/components/assistant-ui/elements/collapsible-root";
import { ToolFallbackRoot } from "@/components/assistant-ui/elements/tool-fallback.aui";
import { Badge } from "@/components/ui/badge";
import copy from "@/data/chat/tool-card.json";
import { cn } from "@/utils/cn";
import type { ToolResult, ToolSource } from "@/utils/thread-messages";
import { formatDuration } from "@/utils/time";

function isToolResult(value: unknown): value is ToolResult {
  return typeof value === "object" && value !== null && "summary" in value;
}

function isToolSource(value: unknown): value is ToolSource {
  return typeof value === "object" && value !== null && "connectorTitle" in value;
}

function Detail({ label, children, mono = true }: { label: string; children: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd>
        {mono ? (
          <pre className="bg-code-surface overflow-x-auto rounded-md px-2.5 py-1.5 text-xs whitespace-pre-wrap break-words">
            <code>{children}</code>
          </pre>
        ) : (
          <span className="text-sm">{children}</span>
        )}
      </dd>
    </div>
  );
}

/** One tool call: what ran, where it came from, how it went, and the details on request. */
export const ToolRow: ToolCallMessagePartComponent = ({ toolName, argsText, result, isError, artifact }) => {
  const detail = isToolResult(result) ? result : null;
  const source = isToolSource(artifact) ? artifact : null;
  const state = result === undefined ? "running" : isError ? "failed" : "ok";
  const Icon = state === "running" ? LoaderIcon : state === "failed" ? XCircleIcon : CheckIcon;
  const label = state === "running" ? copy.running : state === "failed" ? copy.failed : copy.ok;
  const hasDetail = Boolean(argsText || detail?.summary || detail?.preview || source?.reason);

  return (
    <ToolFallbackRoot data-testid="tool-card" data-state={state} className="min-w-0">
      <CollapsibleTrigger
        disabled={!hasDetail}
        className="group/trigger text-muted-foreground hover:text-foreground enabled:cursor-pointer flex w-full min-w-0 items-center gap-2 py-1.5 text-start text-sm transition-colors max-md:min-h-10"
      >
        <Icon
          aria-hidden
          className={cn(
            "size-4 shrink-0",
            state === "running" && "text-live animate-spin [animation-duration:0.8s]",
            state === "failed" && "text-destructive",
          )}
        />
        <span className={cn("text-foreground min-w-0 truncate font-mono text-xs", state === "running" && "shimmer")}>
          {toolName}
        </span>
        {source?.connectorTitle ? (
          <Badge variant="outline" className="shrink-0">
            {source.connectorTitle}
          </Badge>
        ) : null}
        {source?.remoteTool ? (
          <span className="text-muted-foreground min-w-0 truncate font-mono text-xs">{source.remoteTool}</span>
        ) : null}
        <span className={cn("ms-auto shrink-0 text-xs", state === "failed" && "text-destructive")}>{label}</span>
        {detail && detail.durationMs > 0 ? (
          <span className="shrink-0 text-xs tabular-nums">{formatDuration(detail.durationMs)}</span>
        ) : null}
        {hasDetail ? (
          <ChevronDownIcon
            aria-hidden
            className="size-3.5 shrink-0 transition-transform group-data-[panel-open]/trigger:rotate-180"
          />
        ) : null}
      </CollapsibleTrigger>
      <CollapsibleContent className="overflow-hidden">
        <dl className="flex flex-col gap-2 pb-2 ps-6">
          {source?.reason ? (
            <Detail label={copy.reason} mono={false}>
              {source.reason}
            </Detail>
          ) : null}
          {argsText ? <Detail label={copy.arguments}>{argsText}</Detail> : null}
          {detail?.summary ? (
            <Detail label={copy.result} mono={false}>
              {detail.summary}
            </Detail>
          ) : null}
          {detail?.preview ? <Detail label={copy.preview}>{detail.preview}</Detail> : null}
        </dl>
      </CollapsibleContent>
    </ToolFallbackRoot>
  );
};
