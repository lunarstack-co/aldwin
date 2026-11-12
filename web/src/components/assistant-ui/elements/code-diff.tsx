"use client";

import { useId, useState, type ComponentProps } from "react";
import { cn } from "@/utils/cn";
import displaysCopy from "@/data/chat/displays.json";
import { codeScroll, codeSurface, ghostButton, mono, paper } from "./surfaces";

export type DiffKind = "context" | "added" | "removed";

export interface DiffLine {
  kind: DiffKind;
  text: string;
}

const GUTTER: Record<DiffKind, string> = {
  context: "",
  added: "+",
  removed: "−",
};

const KIND_LABEL: Record<DiffKind, string> = {
  context: "Context line: ",
  added: "Added line: ",
  removed: "Removed line: ",
};

export function CodeDiff({
  filename,
  additions,
  deletions,
  lines,
  cycle,
  maxCollapsedLines,
  truncatedLabel,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "filename" | "additions" | "deletions" | "lines" | "cycle"
> & {
  filename: string;
  additions: number;
  deletions: number;
  lines: readonly DiffLine[];
  cycle: number;
  maxCollapsedLines?: number | undefined;
  truncatedLabel?: string | undefined;
}) {
  const [expanded, setExpanded] = useState(false);
  const diffId = useId();
  const canCollapse = maxCollapsedLines !== undefined && lines.length > maxCollapsedLines;
  const shownLines = canCollapse && !expanded ? lines.slice(0, maxCollapsedLines) : lines;

  return (
    <div
      data-slot="code-diff"
      className={cn(
        paper,
        "w-full max-w-full overflow-hidden rounded-2xl font-mono text-xs",
        className,
      )}
      {...props}
    >
      <div className="flex items-center justify-between gap-2 px-4 pt-3 pb-2">
        <span className="text-foreground/90 min-w-0 truncate">{filename}</span>
        <span className={cn(mono, "shrink-0 tabular-nums")}>
          <span className="text-diff-added-mark">
            <span className="sr-only">Added </span>+{additions}
          </span>{" "}
          <span className="text-diff-removed-mark">
            <span className="sr-only">Removed </span>−{deletions}
          </span>
        </span>
      </div>
      <div id={diffId} className={codeScroll}>
        <div className={codeSurface}>
          {shownLines.map((line, i) => (
            <div
              key={`${cycle}-${i}`}
              data-diff-kind={line.kind}
              className={cn(
                "flex border-l-2 border-transparent px-4 py-0.5 leading-relaxed whitespace-pre",
                line.kind === "context" && "text-muted-foreground",
                line.kind === "added" && "border-diff-added-mark bg-diff-added text-foreground",
                line.kind === "removed" && "border-diff-removed-mark bg-diff-removed text-foreground",
              )}
            >
              <span
                aria-hidden
                className={cn(
                  "w-4 shrink-0 font-semibold select-none",
                  line.kind === "added" && "text-diff-added-mark",
                  line.kind === "removed" && "text-diff-removed-mark",
                )}
              >
                {GUTTER[line.kind]}
              </span>
              <span className="sr-only">{KIND_LABEL[line.kind]}</span>
              <span>{line.text}</span>
            </div>
          ))}
        </div>
      </div>
      {canCollapse || truncatedLabel ? (
        <div className="flex flex-wrap items-center gap-2 px-4 pt-1 pb-2.5">
          {canCollapse ? (
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls={diffId}
              onClick={() => setExpanded((v) => !v)}
              className={cn(ghostButton, "h-7 px-2 font-sans text-xs")}
            >
              {expanded ? displaysCopy.showLess : `Show all ${lines.length} lines`}
            </button>
          ) : null}
          {truncatedLabel ? (
            <span className={cn(mono, "text-muted-foreground")}>{truncatedLabel}</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
