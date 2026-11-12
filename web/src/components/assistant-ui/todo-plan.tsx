"use client";

import { ChevronDownIcon } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";

import copy from "@/data/chat/plan.json";
import { useChatStore } from "@/state/chat-store";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";
import { mapTodoStatus, planCarriesOver, planVisibility } from "@/utils/todos";

const TodoListElement = dynamic(
  () =>
    import("@/components/assistant-ui/elements/todo-list").then((m) => m.TodoList),
  { ssr: false },
);

const NARROW = "(max-width: 767px)";

function startsOpen(running: boolean): boolean {
  if (!running || typeof window === "undefined") return false;
  return !window.matchMedia(NARROW).matches;
}

/** The agent's plan above the composer: open while it works, a folded line while it waits, gone once finished. */
export function TodoPlan() {
  const todos = useChatStore((s) => s.todos);
  const activeId = useChatStore((s) => s.activeId);
  const todosTurnId = useChatStore((s) => s.todosTurnId);
  const lastSettled = useChatStore((s) => s.turns.findLast((t) => t.id !== s.activeId));
  const running = activeId !== null;
  const carriesOver = planCarriesOver(todosTurnId, lastSettled);
  const visibility = planVisibility(todos.items, running, running && todosTurnId === activeId, carriesOver);
  // Remembered per working spell, so a new turn opens it again on wide screens.
  const [choice, setChoice] = useState<{ running: boolean; open: boolean } | null>(null);
  if (visibility === "hidden") return null;

  const open = choice?.running === running ? choice.open : startsOpen(running);
  const items = todos.items.map((item, idx) => ({
    id: `todo-${idx}`,
    text: item.text,
    status: mapTodoStatus(item.status),
    description: copy.status[item.status],
  }));

  return (
    <section
      aria-label={copy.label}
      data-testid="todo-list"
      data-open={open || undefined}
      className="border-border/60 bg-background dark:bg-popover rounded-2xl border px-3.5 py-2"
    >
      <button
        type="button"
        aria-expanded={open}
        data-testid="todo-list-toggle"
        title={open ? copy.hide : copy.show}
        onClick={() => setChoice({ running, open: !open })}
        className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 flex w-full cursor-pointer items-center gap-2 rounded-md text-[13.5px] outline-none focus-visible:ring-1 max-md:min-h-10"
      >
        <span className="text-foreground font-medium">{copy.label}</span>
        <span className="font-mono text-xs tabular-nums">
          {fill(copy.progress, { done: todos.done, total: todos.total })}
        </span>
        <ChevronDownIcon
          aria-hidden
          className={cn("ms-auto size-3.5 transition-transform", open && "rotate-180")}
        />
      </button>
      {open ? (
        <TodoListElement
          items={items}
          title={copy.label}
          className="mt-1.5 max-w-full max-h-[40vh] overflow-y-auto [&>div:first-child]:hidden"
        />
      ) : null}
    </section>
  );
}
