"use client";

import { Pencil, Pin, PinOff, Trash2 } from "lucide-react";

import {
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import copy from "@/data/chat/conversation-menu.json";
import { useSession } from "@/providers/chat-session-provider";
import { useChatStore } from "@/state/chat-store";
import { useConversationActions, type ConversationTarget } from "@/state/conversation-actions-store";

/** The open menu: rename, pin or unpin, and delete. */
export default function ConversationMenuItems({
  target,
  side,
  align,
}: {
  target: ConversationTarget;
  side: "bottom" | "right";
  align: "start" | "end";
}) {
  const session = useSession();
  const pinned = useChatStore((s) => s.sessions.find((item) => item.id === target.id)?.pinned === true);
  const { askRename, askDelete } = useConversationActions();

  async function togglePin() {
    const outcome = await session.pinConversation(target.id, !pinned);
    if (!outcome.ok) useChatStore.getState().pushToast(outcome.message);
  }

  return (
    <DropdownMenuContent side={side} align={align} className="w-auto min-w-44" data-testid="conversation-menu">
      <DropdownMenuItem onClick={() => askRename(target)} data-testid="conversation-rename">
        <Pencil aria-hidden />
        {copy.rename}
      </DropdownMenuItem>
      <DropdownMenuItem onClick={() => void togglePin()} data-testid="conversation-pin">
        {pinned ? <PinOff aria-hidden /> : <Pin aria-hidden />}
        {pinned ? copy.unpin : copy.pin}
      </DropdownMenuItem>
      <DropdownMenuSeparator />
      <DropdownMenuItem variant="destructive" onClick={() => askDelete(target)} data-testid="conversation-delete">
        <Trash2 aria-hidden />
        {copy.delete}
      </DropdownMenuItem>
    </DropdownMenuContent>
  );
}
