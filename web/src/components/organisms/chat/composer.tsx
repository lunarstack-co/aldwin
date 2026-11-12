"use client";

import { AuiIf, ComposerPrimitive, useAui } from "@assistant-ui/react";
import { ArrowUp, AtSign, Square } from "lucide-react";
import { useEffect, useId, type KeyboardEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import copy from "@/data/chat/composer.json";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";

/**
 * Where the user types. Send becomes Stop while an answer is on its way, and
 * Esc stops it too; `@` and the model indicator wait for their own screens.
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
  const aui = useAui();
  const active = useChatStore((s) => s.turns.find((t) => t.id === s.activeId) ?? null);
  const stopping = active?.status === "stopping";
  const draft = useChatStore((s) => s.draft);

  useEffect(() => {
    if (draft === null) return;
    aui.composer().setText(draft);
    useChatStore.getState().clearDraft();
  }, [draft, aui]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape" && active !== null && !stopping) {
      event.preventDefault();
      aui.composer().cancel();
    }
  }

  return (
    <ComposerPrimitive.Root
      data-testid="composer"
      data-variant={variant}
      className={cn(
        "border-foreground/10 focus-within:border-foreground/25 flex w-full flex-col gap-2 rounded-thread border bg-[color-mix(in_oklab,var(--color-muted)_30%,transparent)] p-2 transition-[border-color]",
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
        enterKeyHint="send"
        onKeyDown={onKeyDown}
        className="placeholder:text-muted-foreground/60 caret-primary max-h-48 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base leading-6 outline-none"
      />
      <p id={hintId} className="sr-only">
        {copy.hint}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon-sm"
          disabled
          aria-label={copy.skills}
          title={copy.skills}
          data-testid="composer-skills"
          className="max-md:size-10"
        >
          <AtSign aria-hidden />
        </Button>
        <Badge variant="outline" data-testid="composer-model" className="max-w-[50%] truncate">
          {fill(copy.model, { model })}
        </Badge>
        <span aria-hidden className="flex-1" />
        <AuiIf condition={(s) => !s.composer.canCancel}>
          <ComposerPrimitive.Send
            render={
              <Button
                size="icon-sm"
                aria-label={copy.send}
                title={copy.send}
                data-testid="composer-send"
                className="rounded-full max-md:size-10"
              />
            }
          >
            <ArrowUp aria-hidden />
          </ComposerPrimitive.Send>
        </AuiIf>
        <AuiIf condition={(s) => s.composer.canCancel}>
          <ComposerPrimitive.Cancel
            render={
              <Button
                variant="outline"
                size="sm"
                disabled={stopping}
                data-testid="composer-stop"
                className="max-md:h-10"
              />
            }
          >
            <Square aria-hidden className="size-3 fill-current" />
            {copy.stop}
          </ComposerPrimitive.Cancel>
        </AuiIf>
      </div>
    </ComposerPrimitive.Root>
  );
}
