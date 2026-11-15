"use client";

import { AuiIf, ComposerPrimitive, useAui } from "@assistant-ui/react";
import { ArrowUp, Square } from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useId, useRef, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import copy from "@/data/chat/composer.json";
import { selectPendingApproval, useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";

import { AppsPopover } from "./apps-popover";
import { AppsButton, SkillsButton } from "./composer-buttons";
import { ModelPicker } from "./model-picker";
import { PickedAppList } from "./picked-apps";
import { SearchPicker } from "./search-picker";
import { SkillsPopover } from "./skills-popover";

// Loaded once the host has reported context usage, so the composer does not carry it up front.
const ContextRing = dynamic(() => import("./context-ring").then((m) => m.ContextRing), { ssr: false });

function ContextSlot() {
  const hasContext = useChatStore((s) => s.context !== null && s.context.limit > 0);
  return hasContext ? <ContextRing /> : null;
}

/** Stands in for Send while room is being made; aria-disabled keeps it hoverable for the tooltip. */
function WaitForRoomButton() {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          render={
            <Button
              type="button"
              size="icon-sm"
              aria-disabled="true"
              aria-label={copy.waitForRoom}
              data-testid="composer-send"
              data-waiting=""
              onClick={(event) => event.preventDefault()}
              className="cursor-not-allowed rounded-full opacity-50 max-md:size-10"
            />
          }
        >
          <ArrowUp aria-hidden />
        </TooltipTrigger>
        <TooltipContent data-testid="composer-send-wait">{copy.waitForRoom}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/** Send becomes Stop while an answer runs; Esc stops it too. */
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
  const approvalWaiting = useChatStore((s) => selectPendingApproval(s) !== null);
  const inputLocked = disabled || approvalWaiting;
  const stopping = active?.status === "stopping";
  const draft = useChatStore((s) => s.draft);
  const pickedApps = useChatStore((s) => s.pickedApps);
  const makingRoom = useChatStore((s) => s.makingRoomSince !== null);

  useEffect(() => {
    if (draft === null) return;
    aui.composer().setText(draft);
    useChatStore.getState().clearDraft();
  }, [draft, aui]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Typing goes on while room is being made; only sending waits.
    if (makingRoom && event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      return;
    }
    if (event.key === "Escape" && active !== null && !stopping && !approvalWaiting) {
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
        <SkillsPopover />
        <AppsPopover />
        <ComposerPrimitive.Input
          ref={inputRef}
          data-testid="composer-input"
          disabled={inputLocked}
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
        <SkillsButton inputRef={inputRef} disabled={inputLocked} />
        <AppsButton inputRef={inputRef} disabled={inputLocked} />
        <ModelPicker placement="composer" fallback={model} disabled={disabled || active !== null} />
        <SearchPicker disabled={disabled} />
        <ContextSlot />
        <span aria-hidden className="flex-1" />
        {makingRoom ? (
          <WaitForRoomButton />
        ) : (
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
        )}
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
