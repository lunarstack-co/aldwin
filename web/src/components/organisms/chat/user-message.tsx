"use client";

import { MessagePrimitive } from "@assistant-ui/react";

import copy from "@/data/chat/transcript.json";

/** What the user asked, kept to the right so the answer column reads clean. */
export function UserMessage() {
  return (
    <MessagePrimitive.Root className="flex justify-end" data-testid="user-message">
      <div
        aria-label={copy.you}
        className="max-w-[80%] whitespace-pre-wrap rounded-overlay bg-sunken px-4 py-2.5 text-md text-fg"
      >
        <MessagePrimitive.Parts />
      </div>
    </MessagePrimitive.Root>
  );
}
