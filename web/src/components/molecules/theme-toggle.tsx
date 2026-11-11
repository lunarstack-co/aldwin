"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

import { cn } from "@/utils/cn";

export type ThemeLabels = { label: string; light: string; dark: string; system: string };

const OPTIONS = [
  { id: "light", Icon: Sun },
  { id: "dark", Icon: Moon },
  { id: "system", Icon: Monitor },
] as const;

const noop = () => () => {};

/** Light, dark or system, kept in the browser only; never sent to the server. */
export function ThemeToggle({ labels }: { labels: ThemeLabels }) {
  const { theme, setTheme } = useTheme();
  // The prerendered page knows no theme; only the browser does.
  const mounted = useSyncExternalStore(noop, () => true, () => false);
  return (
    <div
      role="radiogroup"
      aria-label={labels.label}
      className="inline-flex rounded-control border border-border-default bg-control p-0.5"
    >
      {OPTIONS.map(({ id, Icon }) => {
        const checked = mounted && theme === id;
        return (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={labels[id]}
            title={labels[id]}
            onClick={() => setTheme(id)}
            className={cn(
              "inline-flex h-7 w-8 cursor-pointer items-center justify-center rounded-[4px] text-fg-secondary transition-[background-color,color] duration-[80ms]",
              checked ? "bg-raised text-fg shadow-[0_1px_0_0_#1f23280a]" : "hover:text-fg",
            )}
          >
            <Icon aria-hidden className="size-4" />
          </button>
        );
      })}
    </div>
  );
}
