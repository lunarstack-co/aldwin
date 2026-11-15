"use client";

import { useThreadViewport } from "@assistant-ui/react";
import { useEffect } from "react";

import { LiveDot } from "@/components/assistant-ui/work";
import copy from "@/data/chat/context.json";
import thinkingCopy from "@/data/chat/thinking.json";
import { useElapsed } from "@/hooks/use-elapsed";
import { useChatStore } from "@/state/chat-store";
import { fill } from "@/utils/text";

/** Shown at the end of the chat while room is made by hand, like the working row of an answer. */
export function MakingRoomRow() {
  const since = useChatStore((s) => s.makingRoomSince);
  const seconds = useElapsed(since ?? 0, null, since !== null);
  const scrollToBottom = useThreadViewport((s) => s.scrollToBottom);

  // Scroll through the thread's viewport; it undoes a plain scroll.
  useEffect(() => {
    if (since === null) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    scrollToBottom({ behavior: still ? "auto" : "smooth" });
  }, [since, scrollToBottom]);

  if (since === null) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={copy.makingLabel}
      data-testid="making-room"
      className="text-muted-foreground flex min-w-0 items-center gap-2 text-sm"
    >
      <LiveDot />
      <span className="shimmer min-w-0 truncate">{copy.makingStatus}</span>
      <span className="shrink-0 tabular-nums">{fill(thinkingCopy.working, { seconds })}</span>
    </div>
  );
}
