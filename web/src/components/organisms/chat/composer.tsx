"use client";

import { AuiIf, ComposerPrimitive, useAui } from "@assistant-ui/react";
import { ArrowUp, AtSign, Blocks, Square } from "lucide-react";
import { useEffect, useId, useRef, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import copy from "@/data/chat/composer.json";
import appsCopy from "@/data/chat/apps-menu.json";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";

import { AppsPopover } from "./apps-popover";
import type { ButtonTrigger } from "./button-trigger";
import { ModelPicker } from "./model-picker";
import { PickedAppList } from "./picked-apps";
import { SearchPicker } from "./search-picker";
import { SkillsPopover } from "./skills-popover";

/**
 * Where the user types. `@` opens the skills menu and `/` the connected apps; Send becomes Stop while an
 * answer is on its way, and Esc stops it too.
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
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const active = useChatStore((s) => s.turns.find((t) => t.id === s.activeId) ?? null);
  const stopping = active?.status === "stopping";
  const draft = useChatStore((s) => s.draft);
  const pickedApps = useChatStore((s) => s.pickedApps);

  useEffect(() => {
    if (draft === null) return;
    aui.composer().setText(draft);
    useChatStore.getState().clearDraft();
  }, [draft, aui]);

  // Types `@` or `/` at the caret as a keystroke would, so the menu opens from the same input event.
  // What it typed is remembered, so closing the menu without a pick takes it back.
  const buttonTriggerRef = useRef<ButtonTrigger | null>(null);
  function startTrigger(char: "@" | "/") {
    const input = inputRef.current;
    if (!input) return;
    input.focus();
    const start = input.selectionStart ?? input.value.length;
    const end = input.selectionEnd ?? start;
    const before = input.value.slice(0, start);
    const inserted = before === "" || /\s$/.test(before) ? char : ` ${char}`;
    buttonTriggerRef.current = {
      char,
      before,
      inserted,
      after: input.value.slice(end),
      picked: false,
      seenOpen: false,
    };
    input.setRangeText(inserted, start, end, "end");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape" && active !== null && !stopping) {
      event.preventDefault();
      aui.composer().cancel();
    }
  }

  return (
    <ComposerPrimitive.Root
      data-testid="composer"
      data-skills-scope=""
      data-variant={variant}
      className={cn(
        "border-foreground/10 focus-within:border-foreground/25 relative flex w-full flex-col gap-2 rounded-thread border bg-[color-mix(in_oklab,var(--color-muted)_30%,transparent)] p-2 transition-[border-color]",
        disabled && "opacity-70",
      )}
    >
      <PickedAppList
        apps={pickedApps}
        onRemove={(name) => useChatStore.getState().unpickApp(name)}
        className="px-1.5 pt-1"
        testId="composer-apps"
      />
      <ComposerPrimitive.TriggerPopoverRoot>
        <SkillsPopover buttonTriggerRef={buttonTriggerRef} />
        <AppsPopover buttonTriggerRef={buttonTriggerRef} />
        <ComposerPrimitive.Input
          ref={inputRef}
          data-testid="composer-input"
          // The skills menu makes this a combobox; the menu sets aria-expanded to true while it is open.
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={false}
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
      </ComposerPrimitive.TriggerPopoverRoot>
      <p id={hintId} className="sr-only">
        {copy.hint}
      </p>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={disabled}
          onClick={() => startTrigger("@")}
          aria-label={copy.skills}
          title={copy.skills}
          data-testid="composer-skills"
          className="max-md:size-10"
        >
          <AtSign aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={disabled}
          onClick={() => startTrigger("/")}
          aria-label={appsCopy.button}
          title={appsCopy.button}
          data-testid="composer-apps-button"
          className="max-md:size-10"
        >
          <Blocks aria-hidden />
        </Button>
        <ModelPicker placement="composer" fallback={model} disabled={disabled || active !== null} />
        <SearchPicker disabled={disabled} />
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
