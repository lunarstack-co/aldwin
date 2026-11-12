"use client";

import type { ComponentProps } from "react";
import { SearchIcon } from "lucide-react";
import { cn } from "@/utils/cn";
import displaysCopy from "@/data/chat/displays.json";
import { safeHref } from "../utils/href";
import { field, mono, ShimmerLabel } from "./surfaces";
import { take } from "../utils/range";

export interface WebSearchResult {
  title: string;
  domain: string;
  url?: string | undefined;
  snippet?: string | undefined;
}

export function WebSearch({
  query,
  results,
  visibleResults,
  searching,
  cycle,
  statusText,
  footer,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "query" | "results" | "visibleResults" | "searching" | "cycle"
> & {
  query: string;
  results: readonly WebSearchResult[];
  visibleResults: number;
  searching: boolean;
  cycle: number;
  statusText?: string | undefined;
  footer?: string | undefined;
}) {
  return (
    <div
      data-slot="web-search"
      className={cn("flex w-full max-w-md flex-col gap-2.5", className)}
      {...props}
    >
      <span
        className={cn(
          field,
          "text-foreground/70 inline-flex max-w-full w-fit items-center gap-1.5 rounded-full px-3.5 py-2 text-xs",
        )}
      >
        <SearchIcon aria-hidden className="text-muted-foreground size-3 shrink-0" />
        <span className="min-w-0 truncate">{query}</span>
      </span>
      <div className="text-muted-foreground text-xs">
        {searching ? (
          <ShimmerLabel className="relative inline-block leading-none">
            {statusText ?? "Searching"}
          </ShimmerLabel>
        ) : (
          <span className="fade-in animate-in duration-300">
            {statusText ??
              (results.length === 0
                ? displaysCopy.search.none
                : `Read ${results.length} ${results.length === 1 ? "source" : "sources"}`)}
          </span>
        )}
      </div>
      {results.length > 0 ? (
        <div className="flex flex-col gap-1">
          {take(results, visibleResults).map((result, index) => {
            const safeUrl = result.url ? safeHref(result.url) : undefined;
            const inner = (
              <>
                <span
                  aria-hidden
                  className="bg-foreground/[0.06] text-muted-foreground flex size-4 shrink-0 items-center justify-center rounded text-[9px] font-medium"
                >
                  {result.domain.charAt(0).toUpperCase() || "•"}
                </span>
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="text-foreground/90 min-w-0 truncate text-[13.5px]">
                    {result.title}
                  </span>
                  {result.snippet ? (
                    <span className="text-muted-foreground line-clamp-2 text-xs">
                      {result.snippet}
                    </span>
                  ) : null}
                </div>
                <span className={cn(mono, "text-muted-foreground shrink-0")}>
                  {result.domain}
                </span>
              </>
            );
            return safeUrl ? (
              <a
                key={`${cycle}-${index}-${result.domain}`}
                href={safeUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both hover:bg-foreground/[0.03] -mx-2.5 flex items-start gap-2.5 rounded-xl px-2.5 py-1.5 transition-colors duration-300"
              >
                {inner}
              </a>
            ) : (
              <div
                key={`${cycle}-${index}-${result.domain}`}
                className="fade-in slide-in-from-bottom-1 animate-in fill-mode-both -mx-2.5 flex items-start gap-2.5 rounded-xl px-2.5 py-1.5"
              >
                {inner}
              </div>
            );
          })}
        </div>
      ) : null}
      {footer ? <p className={cn(mono, "text-muted-foreground")}>{footer}</p> : null}
    </div>
  );
}
