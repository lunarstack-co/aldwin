"use client";

import { AlertCircle } from "lucide-react";

import copy from "@/data/chat/approval.json";
import { selectPendingApproval, useChatStore } from "@/state/chat-store";

export function ApprovalPill() {
  const pending = useChatStore(selectPendingApproval);
  if (!pending) return null;

  const scrollToCard = () => {
    const el = document.querySelector<HTMLElement>(
      `[data-approval-id="${CSS.escape(pending.event.approval_id)}"]`,
    );
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus();
  };

  return (
    <button
      type="button"
      data-testid="approval-pill"
      onClick={scrollToCard}
      className="border-amber-600/30 bg-amber-500/15 text-amber-900 hover:bg-amber-500/25 dark:border-amber-400/30 dark:bg-amber-400/15 dark:text-amber-200 dark:hover:bg-amber-400/25 inline-flex cursor-pointer items-center gap-1.5 self-center rounded-full border px-3.5 py-1.5 text-xs font-medium transition-colors max-md:min-h-10"
    >
      <AlertCircle aria-hidden className="size-3.5 shrink-0" />
      {copy.pill}
    </button>
  );
}
