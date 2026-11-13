"use client";

import { Bot, Globe, Plug, type LucideIcon } from "lucide-react";
import { useEffect, useId, useState } from "react";

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

type AppMarks = Record<string, Mark>;

// Catalogue app marks load on first use, so pages that never show an app do not carry them.
let appMarks: AppMarks | null = null;
let appMarksLoading: Promise<AppMarks> | null = null;

export function loadAppMarks(): Promise<AppMarks> {
  appMarksLoading ??= import("./logos/app-marks").then((m) => (appMarks = m.APP_MARKS));
  return appMarksLoading;
}

/** An app's mark: a hand-kept one first, then a catalogue one; `undefined` while the catalogue marks load. */
function useAppMark(name: string, enabled: boolean): Mark | null | undefined {
  const key = name.toLowerCase();
  const [loaded, setLoaded] = useState<AppMarks | null>(appMarks);
  useEffect(() => {
    if (!enabled || loaded || MARKS[key]) return;
    let live = true;
    void loadAppMarks().then((marks) => {
      if (live) setLoaded(marks);
    });
    return () => {
      live = false;
    };
  }, [enabled, loaded, key]);
  if (!enabled) return null;
  const own = MARKS[key];
  if (own) return own;
  return loaded ? (loaded[key] ?? null) : undefined;
}

function LetterMark({ name, className }: { name: string; className?: string }) {
  return (
    <span
      aria-hidden
      data-brand={name}
      data-generic=""
      data-letter=""
      className={cn(
        "bg-muted text-foreground inline-flex size-4 shrink-0 items-center justify-center rounded-[25%] text-[10px] leading-none font-semibold uppercase",
        className,
      )}
    >
      {name.trim().charAt(0)}
    </span>
  );
}

/** Decorative; the name always sits beside it. */
export function BrandMark({
  name,
  kind,
  className,
}: {
  name: string;
  kind: keyof typeof FALLBACKS;
  className?: string;
}) {
  // SVG url() references are safest with a plain id.
  const gradientId = `mark-${useId().replace(/\W/g, "")}`;
  const appMark = useAppMark(name, kind === "connector");
  const mark = kind === "connector" ? appMark : markFor(name);
  if (mark === undefined) {
    return <span aria-hidden data-brand={name} className={cn("inline-block size-4 shrink-0", className)} />;
  }
  if (mark === null) {
    if (kind === "connector") return <LetterMark name={name} className={className} />;
    const Fallback = FALLBACKS[kind];
    return <Fallback aria-hidden data-brand={name} data-generic="" className={cn("size-4 shrink-0", className)} />;
  }
  const gradient = mark.gradient;
  return (
    <svg
      viewBox={mark.viewBox}
      fill={gradient ? `url(#${gradientId})` : (mark.color ?? "currentColor")}
      fillRule={mark.fillRule}
      aria-hidden
      data-brand={name}
      className={cn("size-4 shrink-0", className)}
    >
      {gradient && (
        <defs>
          <linearGradient id={gradientId} x1={gradient.x1} y1={gradient.y1} x2={gradient.x2} y2={gradient.y2}>
            {gradient.stops.map(([offset, color]) => (
              <stop key={offset} offset={offset} stopColor={color} />
            ))}
          </linearGradient>
        </defs>
      )}
      {mark.paths.map((path, index) => (
        <path key={index} d={path.d} fill={path.color} fillRule={path.fillRule} opacity={path.opacity} />
      ))}
    </svg>
  );
}
