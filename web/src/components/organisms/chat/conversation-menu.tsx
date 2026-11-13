"use client";

import dynamic from "next/dynamic";
import { useState, type ReactElement } from "react";

import { DropdownMenu, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { useConversationActions, type ConversationTarget } from "@/state/conversation-actions-store";

// The dialogs load the first time a rename or delete is asked for, so the chat page does not carry them.
const Dialogs = dynamic(() => import("./conversation-dialogs").then((m) => m.ConversationDialogs), { ssr: false });

// The menu's items load the first time any conversation menu opens.
const Items = dynamic(() => import("./conversation-menu-items"), { ssr: false });

/** Rendered once in the shell; nothing loads until a menu asks for a dialog. */
export function ConversationDialogs() {
  const asked = useConversationActions((s) => s.renaming !== null || s.deleting !== null);
  return asked ? <Dialogs /> : null;
}

/** Rename, pin and delete for one conversation, opened from its rail row or the header title. */
export function ConversationMenu({
  target,
  trigger,
  side = "bottom",
  align = "start",
}: {
  target: ConversationTarget;
  trigger: ReactElement;
  side?: "bottom" | "right";
  align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger render={trigger} />
      {open ? <Items target={target} side={side} align={align} /> : null}
    </DropdownMenu>
  );
}
