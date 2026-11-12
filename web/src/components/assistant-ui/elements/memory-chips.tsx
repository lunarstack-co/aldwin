"use client";

import type { ComponentProps } from "react";
import { BrainIcon, XIcon } from "lucide-react";
import { cn } from "@/utils/cn";
import { field, ghostButton, mono } from "./surfaces";

export type MemoryChange = "added" | "updated" | "replaced" | "removed" | "existing";

export interface MemoryChip {
  id: string;
  text: string;
  change: MemoryChange;
  label?: string | undefined;
}

export function MemoryChips({
  chips,
  headerLabel,
  onForget,
  className,
  ...props
}: Omit<ComponentProps<"div">, "children" | "chips" | "onForget"> & {
  chips: readonly MemoryChip[];
  headerLabel?: string | undefined;
  onForget?: (id: string) => void;
}) {
  const fresh = chips.filter((chip) => chip.change !== "existing").length;

  return (
    <div
      data-slot="memory-chips"
      className={cn("flex w-full max-w-md flex-col gap-2", className)}
      {...props}
    >
      <div className="flex items-center gap-1.5">
        <BrainIcon aria-hidden className="text-muted-foreground size-3.5" />
        <span className={cn(mono, "text-muted-foreground")}>
          {headerLabel ?? (fresh > 0 ? `remembered ${fresh}` : "memory")}
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {chips.map((chip) => (
          <span
            key={chip.id}
            data-memory-change={chip.change}
            className={cn(
              "fade-in zoom-in-95 animate-in fill-mode-both group flex items-center gap-1.5 rounded-full py-1 pr-2.5 pl-2.5 text-xs duration-300",
              chip.change === "existing"
                ? cn(field, "text-muted-foreground")
                : chip.change === "removed"
                  ? "bg-red-500/12 text-red-700 dark:bg-red-400/15 dark:text-red-300"
                  : chip.change === "replaced" || chip.change === "updated"
                    ? "bg-amber-500/12 text-amber-800 dark:bg-amber-400/15 dark:text-amber-300"
                    : "bg-blue-500/12 text-blue-700 dark:bg-blue-400/15 dark:text-blue-300",
            )}
          >
            {chip.label ? (
              <span className={cn(mono, "font-medium")}>{chip.label}</span>
            ) : null}
            <span className="break-words">{chip.text}</span>
            {onForget && (
              <button
                type="button"
                aria-label={`Forget "${chip.text}"`}
                onClick={() => onForget(chip.id)}
                className={cn(ghostButton, "size-4")}
              >
                <XIcon className="size-2.5" />
              </button>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}
