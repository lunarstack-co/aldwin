"use client";

import { AuiIf, ComposerPrimitive } from "@assistant-ui/react";
import { ArrowUp, AtSign, Square } from "lucide-react";
import { useId } from "react";

import { Button } from "@/components/atoms/button";
import { Chip } from "@/components/atoms/chip";
import copy from "@/data/chat/composer.json";
import { useElapsed } from "@/hooks/use-elapsed";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";

/**
 * Where the user types. Send becomes Stop while an answer is on its way; `@`
 * and the model indicator are placeholders until their own screens exist.
 */
export function Composer({
  variant,
  model,
  disabled,
}: {
  variant: "centered" | "docked";
  model: string;
  disabled: boolean;
}) {
  const hintId = useId();
  const active = useChatStore((s) => s.turns.find((t) => t.id === s.activeId) ?? null);
  const seconds = useElapsed(active?.startedAt ?? 0, null, active !== null);
  const stopping = active?.status === "stopping";
  const status = active === null ? "" : stopping ? copy.stopping : fill(copy.working, { seconds });

  return (
    <ComposerPrimitive.Root
      data-testid="composer"
      data-variant={variant}
      className={cn(
        "flex w-full flex-col gap-2 rounded-overlay border border-border-default bg-raised p-3 shadow-popover transition-[border-color] duration-[150ms] focus-within:border-fg-tertiary",
        disabled && "opacity-70",
      )}
    >
      <ComposerPrimitive.Input
        data-testid="composer-input"
        aria-label={copy.label}
        aria-describedby={hintId}
        placeholder={variant === "centered" ? copy.placeholder : copy.followUp}
        rows={1}
        maxRows={8}
        autoFocus={variant === "centered"}
        className="w-full resize-none bg-transparent px-1 py-1 text-md text-fg outline-none placeholder:text-fg-tertiary"
      />
      <p id={hintId} className="sr-only">
        {copy.hint}
      </p>
      <div className="flex items-center gap-2">
        <Button icon disabled aria-label={copy.skills} title={copy.skills} data-testid="composer-skills">
          <AtSign aria-hidden className="size-4" />
        </Button>
        <Chip data-testid="composer-model">{fill(copy.model, { model })}</Chip>
        <p role="status" aria-live="polite" className="min-w-0 flex-1 truncate text-xs text-fg-secondary">
          {status}
        </p>
        <AuiIf condition={(s) => !s.composer.canCancel}>
          <ComposerPrimitive.Send asChild>
            <Button
              variant="primary"
              icon
              aria-label={copy.send}
              title={copy.send}
              data-testid="composer-send"
            >
              <ArrowUp aria-hidden className="size-4" />
            </Button>
          </ComposerPrimitive.Send>
        </AuiIf>
        <AuiIf condition={(s) => s.composer.canCancel}>
          <ComposerPrimitive.Cancel asChild>
            <Button variant="outline" disabled={stopping} data-testid="composer-stop">
              <Square aria-hidden className="size-3.5 fill-current" />
              {copy.stop}
            </Button>
          </ComposerPrimitive.Cancel>
        </AuiIf>
      </div>
    </ComposerPrimitive.Root>
  );
}
