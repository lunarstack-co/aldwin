"use client";

import { ComposerPrimitive, useAui, useAuiState } from "@assistant-ui/react";
import { useEffect, type MutableRefObject } from "react";

/** What the `@` or apps button typed, so browsing without a pick leaves the message as it was. */
export type ButtonTrigger = {
  char: "@" | "/";
  before: string;
  inserted: string;
  after: string;
  picked: boolean;
  /** The menu has opened since the button typed; only then does a close count. */
  seenOpen: boolean;
};

export type ButtonTriggerRef = MutableRefObject<ButtonTrigger | null>;

/** Marks the button-opened menu as used, so closing it keeps the pick. */
export function markPicked(ref: ButtonTriggerRef | undefined, char: "@" | "/"): void {
  if (ref?.current?.char === char) ref.current.picked = true;
}

/** On close without a pick, removes what the button typed and any search word; text typed past the menu stays. */
export function ClearUnusedTrigger({ char, triggerRef }: { char: "@" | "/"; triggerRef: ButtonTriggerRef }) {
  const popover = ComposerPrimitive.unstable_useTriggerPopoverScopeContext();
  const aui = useAui();
  const text = useAuiState((s) => s.composer.text);

  // The popover remounts its children when it opens and closes, so whether it was open lives on the shared record.
  useEffect(() => {
    const opened = triggerRef.current;
    if (opened?.char !== char) return;
    if (popover.open) {
      opened.seenOpen = true;
      return;
    }
    if (!opened.seenOpen) return;
    triggerRef.current = null;
    const unchangedAround = text.startsWith(opened.before) && text.endsWith(opened.after);
    const added = text.slice(opened.before.length, text.length - opened.after.length);
    const onlySearch = added.startsWith(opened.inserted) && !/\s/.test(added.slice(opened.inserted.length));
    if (!opened.picked && unchangedAround && onlySearch) aui.composer().setText(opened.before + opened.after);
    // An app pick removes the `/` and its search but not the space the button put before it.
    else if (opened.picked && unchangedAround && added === opened.inserted.slice(0, -1) && added !== "")
      aui.composer().setText(opened.before + opened.after);
  }, [popover.open, text, char, triggerRef, aui]);

  return null;
}
