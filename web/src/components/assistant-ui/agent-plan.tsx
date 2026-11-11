"use client";

import { CheckIcon, ChevronDownIcon, CircleIcon, MinusIcon } from "lucide-react";
import { useState } from "react";

import copy from "@/data/chat/plan.json";
import { useChatStore } from "@/state/chat-store";
import type { TodoStatus } from "@/types/protocol";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";

import { LiveDot } from "./work";

function StatusIcon({ status }: { status: TodoStatus }) {
  if (status === "done") return <CheckIcon aria-hidden className="size-3.5 shrink-0" />;
  if (status === "dropped") return <MinusIcon aria-hidden className="size-3.5 shrink-0" />;
  if (status === "in_progress") return <LiveDot className="mx-1 shrink-0" />;
  return <CircleIcon aria-hidden className="size-3.5 shrink-0" />;
}

/** The agent's working list for this conversation, above the composer while it has items. */
export function AgentPlan() {
  const todos = useChatStore((s) => s.todos);
  const [open, setOpen] = useState(true);
  if (todos.total === 0) return null;
  return (
    <section
      aria-label={copy.label}
      data-testid="agent-plan"
      className="bg-foreground/[0.025] dark:bg-foreground/[0.04] rounded-lg px-3 py-2 text-sm"
    >
      <button
        type="button"
        aria-expanded={open}
        title={open ? copy.hide : copy.show}
        onClick={() => setOpen(!open)}
        className="text-foreground/70 hover:text-foreground flex w-full cursor-pointer items-center gap-2 max-md:min-h-10"
      >
        <span className="text-foreground font-medium">{copy.label}</span>
        <span className="tabular-nums" data-testid="agent-plan-progress">
          {fill(copy.progress, { done: todos.done, total: todos.total })}
        </span>
        <ChevronDownIcon aria-hidden className={cn("ms-auto size-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open ? (
        <ol className="mt-1.5 flex flex-col gap-1">
          {todos.items.map((item, index) => (
            <li
              key={`${index}-${item.text}`}
              data-status={item.status}
              className={cn(
                "flex items-center gap-2",
                item.status === "done" && "text-foreground/70",
                item.status === "dropped" && "text-foreground/70 line-through",
              )}
            >
              <StatusIcon status={item.status} />
              <span className="min-w-0 break-words">{item.text}</span>
              <span className="sr-only">{copy.status[item.status]}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}
