import type { ReactNode } from "react";

import { cn } from "@/utils/cn";

export type Tone = "ok" | "attention" | "off";

const DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  attention: "bg-amber-500",
  off: "bg-muted-foreground/50",
};

/** The words carry the meaning; the dot only helps scanning. */
export function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span
      data-tone={tone}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        tone === "ok" && "border-emerald-500/30 bg-emerald-500/10",
        tone === "attention" && "border-amber-500/40 bg-amber-500/10",
      )}
    >
      <span aria-hidden className={cn("size-1.5 rounded-full", DOT[tone])} />
      {children}
    </span>
  );
}

const ORDER: Record<Tone, number> = { ok: 0, attention: 1, off: 2 };

/** Working things first, then those that need the user, then the rest; ties keep the host's order. */
export function byTone<T>(items: T[], toneOf: (item: T) => Tone): T[] {
  return [...items].sort((a, b) => ORDER[toneOf(a)] - ORDER[toneOf(b)]);
}
