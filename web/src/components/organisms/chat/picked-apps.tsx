"use client";

import { X } from "lucide-react";

import { BrandMark } from "@/components/brand/brand-mark";
import copy from "@/data/chat/apps-menu.json";
import type { PickedApp } from "@/state/chat-store";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";

/** One picked app as a chip: its mark, its name, and a remove button while the message is being written. */
export function AppChip({ app, onRemove }: { app: PickedApp; onRemove?: () => void }) {
  return (
    <span
      data-picked-app={app.name}
      className={cn(
        "bg-background inline-flex max-w-full items-center gap-1.5 rounded-md border py-0.5 text-xs font-medium",
        onRemove ? "ps-2 pe-0.5" : "px-2",
      )}
    >
      <BrandMark name={app.name} kind="connector" className="size-3.5" />
      <span className="truncate">{app.title}</span>
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          aria-label={fill(copy.remove, { app: app.title })}
          className="hover:bg-accent text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 inline-flex size-5 items-center justify-center rounded outline-none focus-visible:ring-1 max-md:size-8"
        >
          <X aria-hidden className="size-3" />
        </button>
      ) : null}
    </span>
  );
}

export function PickedAppList({
  apps,
  onRemove,
  className,
  testId,
}: {
  apps: PickedApp[];
  onRemove?: (name: string) => void;
  className?: string;
  testId?: string;
}) {
  if (apps.length === 0) return null;
  return (
    <ul aria-label={copy.picked} data-testid={testId} className={cn("flex flex-wrap gap-1.5", className)}>
      {apps.map((app) => (
        <li key={app.name} className="max-w-full">
          <AppChip app={app} onRemove={onRemove ? () => onRemove(app.name) : undefined} />
        </li>
      ))}
    </ul>
  );
}
