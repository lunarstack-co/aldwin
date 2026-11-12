"use client";

import type { ComponentProps } from "react";
import { cn } from "@/utils/cn";
import { mono, paper } from "./surfaces";
import { announced, pct } from "../utils/range";

const fmt = (n: number) => n.toLocaleString("en-US");

export interface ContextSegment {
  label: string;
  tokens: number;
  tint: string;
}

export function ContextBreakdown({
  segments,
  limit,
  usedOverride,
  title = "Context",
  headroomLabel = "Headroom",
  formatTokens,
  className,
  ...props
}: Omit<ComponentProps<"div">, "children" | "segments" | "limit" | "title"> & {
  segments: readonly ContextSegment[];
  limit: number;
  usedOverride?: number | undefined;
  title?: string | undefined;
  headroomLabel?: string | undefined;
  formatTokens?: ((tokens: number) => string) | undefined;
}) {
  const sumTokens = segments.reduce((sum, segment) => sum + segment.tokens, 0);
  const used = usedOverride ?? sumTokens;
  const isAmber = limit > 0 && used * 100 > limit * 85;
  const share = (tokens: number) => pct(tokens, limit);

  return (
    <div
      data-slot="context-breakdown"
      data-testid="context-breakdown"
      className={cn(
        paper,
        "flex w-full max-w-sm flex-col gap-3 rounded-2xl p-4",
        className,
      )}
      {...props}
    >
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-[13.5px] font-medium">{title}</span>
        <span
          className={cn(
            mono,
            "tabular-nums",
            isAmber
              ? "text-amber-700 dark:text-amber-400"
              : "text-muted-foreground",
          )}
        >
          {fmt(used)} / {fmt(limit)}
        </span>
      </div>

      <div className="bg-foreground/[0.06] inset-ring-border flex h-2 w-full overflow-hidden rounded-full inset-ring forced-colors:border">
        {segments.map((segment) => {
          const width = share(segment.tokens);
          if (announced(width) === 0) return null;
          return (
            <span
              key={segment.label}
              role="meter"
              aria-label={`${segment.label} context usage`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={announced(width)}
              aria-valuetext={`${fmt(segment.tokens)} of ${fmt(limit)}`}
              className={cn(
                "h-full transition-[width] duration-500 ease-out forced-color-adjust-none motion-reduce:transition-none",
                segment.tint,
              )}
              style={{ width: `${width}%` }}
            />
          );
        })}
      </div>

      <div className="flex flex-col gap-1.5">
        {segments.map((segment) => (
          <div
            key={segment.label}
            data-testid="context-segment"
            className="flex items-center gap-2"
          >
            <span
              aria-hidden
              className={cn(
                "size-2 shrink-0 rounded-full forced-color-adjust-none",
                segment.tint,
              )}
            />
            <span className="text-foreground/75 min-w-0 flex-1 truncate text-[13px]">
              {segment.label}
            </span>
            <span
              className={cn(
                mono,
                "text-muted-foreground shrink-0 tabular-nums",
              )}
            >
              {formatTokens ? formatTokens(segment.tokens) : fmt(segment.tokens)}
            </span>
          </div>
        ))}
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className="bg-foreground/[0.08] inset-ring-border size-2 shrink-0 rounded-full inset-ring forced-colors:border"
          />
          <span className="text-muted-foreground min-w-0 flex-1 truncate text-[13px]">
            {headroomLabel}
          </span>
          <span
            className={cn(mono, "text-muted-foreground shrink-0 tabular-nums")}
          >
            {fmt(Math.max(0, limit - used))}
          </span>
        </div>
      </div>
    </div>
  );
}
