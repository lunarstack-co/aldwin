"use client";

import { MessagePrimitive } from "@assistant-ui/react";

import copy from "@/data/chat/transcript.json";

/** What the user asked, kept to the right so the answer column reads clean. */
export function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-end" data-testid="user-message">
      <div
        aria-label={copy.you}
        className="bg-muted text-foreground max-w-[80%] rounded-thread px-4 py-2.5 text-base whitespace-pre-wrap break-words"
      >
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}
