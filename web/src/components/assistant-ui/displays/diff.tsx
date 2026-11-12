"use client";

import { CodeDiff } from "@/components/assistant-ui/elements/code-diff";
import copy from "@/data/chat/displays.json";
import type { DiffDisplay } from "@/types/protocol";
import { fill } from "@/utils/text";

export function DiffDisplayView({
  display,
  maxCollapsedLines = 20,
}: {
  display: DiffDisplay;
  maxCollapsedLines?: number;
}) {
  return (
    <CodeDiff
      data-testid="display-diff"
      filename={display.path}
      additions={display.additions}
      deletions={display.deletions}
      lines={display.lines}
      cycle={1}
      maxCollapsedLines={maxCollapsedLines}
      truncatedLabel={
        display.truncated
          ? fill(copy.truncated, { count: display.lines.length })
          : undefined
      }
      className="max-w-full"
    />
  );
}
