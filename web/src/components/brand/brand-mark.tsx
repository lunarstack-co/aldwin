import { Bot, Globe, Plug, type LucideIcon } from "lucide-react";

import { cn } from "@/utils/cn";

import { MARKS, type Mark } from "./logos/marks";

const FALLBACKS: Record<"provider" | "search" | "connector", LucideIcon> = {
  provider: Bot,
  search: Globe,
  connector: Plug,
};

/** The bundled mark for a provider, search engine or app, by the name the host uses; `null` when there is none. */
export function markFor(name: string): Mark | null {
  return MARKS[name.toLowerCase()] ?? null;
}

/** A brand mark in its own colour (or the text colour for black and white marks), or a generic icon for names without one. Decorative: the name sits beside it. */
export function BrandMark({
  name,
  kind,
  className,
}: {
  name: string;
  kind: keyof typeof FALLBACKS;
  className?: string;
}) {
  const mark = markFor(name);
  if (mark === null) {
    const Fallback = FALLBACKS[kind];
    return <Fallback aria-hidden data-brand={name} data-generic="" className={cn("size-4 shrink-0", className)} />;
  }
  return (
    <svg
      viewBox={mark.viewBox}
      fill={mark.color ?? "currentColor"}
      fillRule={mark.fillRule}
      aria-hidden
      data-brand={name}
      className={cn("size-4 shrink-0", className)}
    >
      {mark.paths.map((path, index) => (
        <path key={index} d={path.d} fillRule={path.fillRule} opacity={path.opacity} />
      ))}
    </svg>
  );
}
