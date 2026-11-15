"use client";

import { useLayoutEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import copy from "@/data/settings/advanced.json";
import common from "@/data/settings/common.json";

function scrollParent(element: HTMLElement): HTMLElement | null {
  for (let node = element.parentElement; node; node = node.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(node).overflowY)) return node;
  }
  return null;
}

/** Pinned to the bottom while there are edits; in the page flow, so it never covers the last setting. */
export function SaveBar({
  saved,
  busy,
  canSave,
  needsFix,
  error,
  onDiscard,
}: {
  saved: boolean;
  busy: boolean;
  canSave: boolean;
  needsFix: boolean;
  error: string | null;
  onDiscard: () => void;
}) {
  const bar = useRef<HTMLDivElement>(null);

  // Keep the bar's height clear, and bring the edited setting and its error out from under it.
  useLayoutEffect(() => {
    const element = bar.current;
    const scroller = element ? scrollParent(element) : null;
    if (!element || !scroller) return;
    const before = scroller.style.scrollPaddingBottom;
    const clearance = Math.ceil(element.offsetHeight) + 12;
    scroller.style.scrollPaddingBottom = `${clearance}px`;
    const active = document.activeElement;
    // Measured against where the bar comes to rest, not where its slide-in starts.
    const restingTop = scroller.getBoundingClientRect().bottom - clearance;
    if (active instanceof HTMLElement && scroller.contains(active) && !element.contains(active)) {
      const control = active.closest<HTMLElement>("[data-slot=setting-control]") ?? active;
      if (control.getBoundingClientRect().bottom > restingTop) control.scrollIntoView({ block: "nearest" });
    }
    return () => {
      scroller.style.scrollPaddingBottom = before;
    };
  }, [needsFix, error]);

  return (
    <div
      ref={bar}
      role="region"
      aria-label={copy.bar.label}
      data-testid="save-bar"
      className="bg-background sticky bottom-0 z-10 mt-auto border-t pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-2 motion-safe:duration-150"
    >
      <div className="flex flex-col gap-3 @sm:flex-row @sm:items-center @sm:justify-between">
        <div className="flex min-w-0 flex-col gap-0.5 text-sm">
          {saved ? (
            <span role="status">{common.saved}</span>
          ) : (
            <span className="font-medium">{copy.bar.unsaved}</span>
          )}
          {error ? (
            <p role="alert" className="text-destructive">
              {error}
            </p>
          ) : needsFix ? (
            <p className="text-muted-foreground">{copy.bar.fix}</p>
          ) : null}
        </div>
        {saved ? null : (
          <div className="flex shrink-0 gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={onDiscard}
              disabled={busy}
              data-testid="advanced-discard"
              className="max-md:h-11 max-md:flex-1"
            >
              {copy.bar.discard}
            </Button>
            <Button
              type="submit"
              disabled={!canSave || busy}
              data-testid="advanced-save"
              className="max-md:h-11 max-md:flex-1"
            >
              {busy ? common.saving : common.save}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}
