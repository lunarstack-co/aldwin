"use client";

import type { ComponentProps } from "react";
import { ArrowRightIcon, SquareIcon } from "lucide-react";
import { cn } from "@/utils/cn";
import { field, mono } from "./surfaces";

export function StoppedRun({
  words,
  reason,
  continueLabel = "Continue",
  discardLabel = "Discard",
  onContinue,
  onDiscard,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "words" | "reason" | "onContinue" | "onDiscard"
> & {
  words: readonly string[];
  reason: string;
  continueLabel?: string | undefined;
  discardLabel?: string | undefined;
  onContinue?: () => void;
  onDiscard?: () => void;
}) {
  const joined = words.join(" ");
  return (
    <div
      data-slot="stopped-run"
      data-testid="stopped-run"
      className={cn("flex w-full max-w-full flex-col gap-3", className)}
      {...props}
    >
      {joined ? (
        <p className="text-foreground/85 text-base leading-relaxed whitespace-pre-wrap break-words">
          {joined}
          <span
            aria-hidden
            className="bg-foreground/20 ms-1 inline-block h-[1em] w-[2px] translate-y-[0.15em] rounded-full"
          />
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <span
          role="status"
          className={cn(
            field,
            mono,
            // Muted text on the tinted pill falls just under 4.5:1 in light mode.
            "text-foreground/80 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1",
          )}
        >
          <SquareIcon aria-hidden className="size-2.5 fill-current" />
          {reason}
        </span>

        {onContinue ? (
          <button
            type="button"
            data-testid="stopped-continue"
            onClick={onContinue}
            className="text-foreground/80 hover:bg-foreground/[0.06] hover:text-foreground ms-auto flex h-8 cursor-pointer items-center gap-1 rounded-full px-3 text-xs font-medium transition-[background-color,color,scale] duration-150 active:scale-[0.96] max-md:min-h-10"
          >
            {continueLabel}
            <ArrowRightIcon aria-hidden className="size-3" />
          </button>
        ) : null}
        {onDiscard ? (
          <button
            type="button"
            data-testid="stopped-discard"
            onClick={onDiscard}
            className="text-muted-foreground hover:bg-foreground/[0.06] hover:text-foreground flex h-8 cursor-pointer items-center rounded-full px-3 text-xs font-medium transition-[background-color,color,scale] duration-150 active:scale-[0.96] max-md:min-h-10"
          >
            {discardLabel}
          </button>
        ) : null}
      </div>
    </div>
  );
}
