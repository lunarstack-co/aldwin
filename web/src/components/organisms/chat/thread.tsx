"use client";

import { ThreadPrimitive } from "@assistant-ui/react";
import { ArrowDown } from "lucide-react";

import { Button } from "@/components/atoms/button";
import copy from "@/data/chat/transcript.json";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";

import { AssistantMessage } from "./assistant-message";
import { Composer } from "./composer";
import { EmptyHome, TrustLine } from "./empty-home";
import { UserMessage } from "./user-message";

const components = { UserMessage, AssistantMessage };

/**
 * The conversation and its composer: centred on an empty conversation, with
 * the composer docked to the bottom once there is something to read.
 */
export function Thread({ model, disabled }: { model: string; disabled: boolean }) {
  const isEmpty = useChatStore((s) => s.turns.length === 0);
  return (
    <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
      <ThreadPrimitive.Viewport
        turnAnchor="top"
        data-testid="transcript"
        className="relative flex min-h-0 flex-1 flex-col overflow-y-auto"
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-[720px] flex-1 flex-col px-4 sm:px-6",
            isEmpty && "justify-center",
          )}
        >
          <ThreadPrimitive.Empty>
            <EmptyHome />
          </ThreadPrimitive.Empty>
          <section aria-label={copy.label} className="flex flex-col gap-8 py-6 empty:hidden">
            <ThreadPrimitive.Messages components={components} />
          </section>
          <ThreadPrimitive.ViewportFooter
            className={cn(
              "relative flex flex-col gap-3 pb-4",
              !isEmpty && "sticky bottom-0 mt-auto bg-base pt-2",
            )}
          >
            <ThreadPrimitive.ScrollToBottom asChild>
              <Button
                variant="outline"
                icon
                aria-label={copy.scrollToBottom}
                className="absolute -top-10 self-center rounded-full shadow-popover disabled:invisible"
              >
                <ArrowDown aria-hidden className="size-4" />
              </Button>
            </ThreadPrimitive.ScrollToBottom>
            <Composer variant={isEmpty ? "centered" : "docked"} model={model} disabled={disabled} />
            <ThreadPrimitive.Empty>
              <TrustLine />
            </ThreadPrimitive.Empty>
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
