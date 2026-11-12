"use client";

import dynamic from "next/dynamic";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import copy from "@/data/chat/context.json";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";
import { contextPercent, contextTone } from "@/utils/context";
import { fill } from "@/utils/text";

const ContextBreakdownLazy = dynamic(
  () =>
    import("@/components/assistant-ui/elements/context-breakdown").then(
      (m) => m.ContextBreakdown,
    ),
  { ssr: false },
);

const TINTS = [
  "bg-blue-500",
  "bg-indigo-500",
  "bg-emerald-500",
  "bg-teal-500",
  "bg-cyan-500",
  "bg-amber-500",
  "bg-violet-500",
  "bg-rose-500",
];

export function ContextRing() {
  const context = useChatStore((s) => s.context);
  if (!context || context.limit <= 0) return null;

  const percent = contextPercent(context.used, context.limit);
  const tone = contextTone(context.used, context.limit);
  const amber = tone === "amber";

  const radius = 6;
  const circumference = 2 * Math.PI * radius;
  const strokeOffset = circumference - (Math.min(100, percent) / 100) * circumference;

  const visibleSegments = context.segments.filter(
    (seg) => seg.key !== "connectors" || seg.tokens > 0 || context.segments.length <= 7,
  );
  const breakdownSegments = visibleSegments.map((seg, idx) => ({
    label: seg.label,
    tokens: seg.tokens,
    tint: TINTS[idx % TINTS.length] ?? "bg-blue-500",
  }));

  const summaryTitle = fill(copy.triggerTitle, {
    used: context.used.toLocaleString("en-US"),
    limit: context.limit.toLocaleString("en-US"),
    percent,
  });

  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            data-testid="context-ring"
            data-tone={tone}
            aria-label={`${copy.label}: ${summaryTitle}`}
            title={summaryTitle}
            className={cn(
              "gap-1.5 rounded-md px-2 font-mono text-xs tabular-nums max-md:h-10 max-md:px-1.5",
              amber
                ? "text-amber-700 hover:text-amber-800 dark:text-amber-400 dark:hover:text-amber-300"
                : "text-muted-foreground",
            )}
          />
        }
      >
        <svg aria-hidden width="16" height="16" viewBox="0 0 16 16" className="-rotate-90 shrink-0">
          <circle
            cx="8"
            cy="8"
            r={radius}
            fill="none"
            strokeWidth="2"
            className="stroke-foreground/15"
          />
          <circle
            cx="8"
            cy="8"
            r={radius}
            fill="none"
            strokeWidth="2"
            strokeDasharray={circumference}
            strokeDashoffset={strokeOffset}
            strokeLinecap="round"
            className={cn(amber ? "stroke-amber-600 dark:stroke-amber-400" : "stroke-foreground/70")}
          />
        </svg>
        <span>{fill(copy.percent, { percent })}</span>
      </PopoverTrigger>
      <PopoverContent align="start" sideOffset={8} className="w-80 rounded-2xl border-0 bg-transparent p-0 shadow-none ring-0">
        <ContextBreakdownLazy
          limit={context.limit}
          usedOverride={context.used}
          segments={breakdownSegments}
          title={copy.title}
          headroomLabel={copy.headroom}
          formatTokens={(t) => fill(copy.about, { tokens: t.toLocaleString("en-US") })}
        />
      </PopoverContent>
    </Popover>
  );
}
