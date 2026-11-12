"use client";

import { ThreadPrimitive } from "@assistant-ui/react";
import { ArrowDown } from "lucide-react";

import { AgentPlan } from "@/components/assistant-ui/agent-plan";
import { Button } from "@/components/ui/button";
import copy from "@/data/chat/transcript.json";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { cn } from "@/utils/cn";

import { AssistantMessage } from "./assistant-message";
import { Composer } from "./composer";
import { EmptyHome } from "./empty-home";
import { UserMessage } from "./user-message";

const components = { UserMessage, AssistantMessage };

/**
 * The conversation and its composer: centred on an empty conversation, with
 * the composer docked to the bottom once there is something to read.
 */
export function Thread({ model, disabled }: { model: string; disabled: boolean }) {
  const isEmpty = useChatStore((s) => s.turns.length === 0);
  // Until the first history arrives the conversation is unknown, so show nothing rather than the wrong screen.
  // A host that is not serving yet sends no history, so its waiting state shows at once.
  const historyLoaded = useChatStore((s) => s.historyLoaded);
  const notReady = useConnectionStore((s) => s.phase === "not_ready");
  const settled = historyLoaded || notReady;
  return (
    <ThreadPrimitive.Root
      data-testid="thread"
      data-settled={settled}
      className={cn("flex min-h-0 flex-1 flex-col transition-opacity duration-300 ease-out", !settled && "opacity-0")}
    >
      <ThreadPrimitive.Viewport
        turnAnchor="top"
        data-testid="transcript"
        className="relative flex min-h-0 flex-1 flex-col overflow-x-hidden overflow-y-auto"
      >
        <div
          className={cn(
            "mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col px-4 sm:px-6 xl:max-w-3xl",
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
              "bg-background relative flex flex-col gap-3 pb-[max(1rem,env(safe-area-inset-bottom))]",
              !isEmpty && "sticky bottom-0 mt-auto pt-2",
            )}
          >
            <ThreadPrimitive.ScrollToBottom
              render={
                <Button
                  variant="outline"
                  size="icon"
                  aria-label={copy.scrollToBottom}
                  title={copy.scrollToBottom}
                  className="bg-background absolute -top-11 self-center rounded-full disabled:invisible max-md:size-10"
                />
              }
            >
              <ArrowDown aria-hidden />
            </ThreadPrimitive.ScrollToBottom>
            <AgentPlan />
            <Composer variant={isEmpty ? "centered" : "docked"} model={model} disabled={disabled} />
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
