"use client";

import { ThreadPrimitive } from "@assistant-ui/react";
import { ArrowDown } from "lucide-react";
import dynamic from "next/dynamic";

import { Button } from "@/components/ui/button";
import copy from "@/data/chat/transcript.json";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { cn } from "@/utils/cn";

import { ApprovalPill } from "./approval-pill";
import { AssistantMessage } from "./assistant-message";
import { Composer } from "./composer";
import { EmptyHome } from "./empty-home";
import { UserMessage } from "./user-message";

// Loaded only once a plan exists, so the chat page does not carry it up front.
const TodoPlan = dynamic(() => import("@/components/assistant-ui/todo-plan").then((m) => m.TodoPlan), {
  ssr: false,
});

function PlanSlot() {
  const hasPlan = useChatStore((s) => s.todos.total > 0);
  return hasPlan ? <TodoPlan /> : null;
}

const components = { UserMessage, AssistantMessage };

/**
 * The conversation and its composer: centred on an empty conversation (at the
 * bottom on phones), with the composer docked to the bottom once there is
 * something to read.
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
            // Phones keep the composer at the bottom, where the thumb is; wider screens centre it with the greeting.
            isEmpty && "md:justify-center",
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
            <ApprovalPill />
            <PlanSlot />
            <Composer variant={isEmpty ? "centered" : "docked"} model={model} disabled={disabled} />
          </ThreadPrimitive.ViewportFooter>
        </div>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
