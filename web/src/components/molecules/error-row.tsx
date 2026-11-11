"use client";

import { ActionBarPrimitive } from "@assistant-ui/react";
import { Copy, RotateCcw, TriangleExclamationPoint } from "lucide-react";

import { Button } from "@/components/atoms/button";
import { useCopy } from "@/hooks/use-copy";

export type ErrorLabels = { label: string; retry: string; copyError: string; copied: string };

/** A turn that failed: the reason, a Retry as a new turn, and the reason on the clipboard. */
export function ErrorRow({ message, labels }: { message: string; labels: ErrorLabels }) {
  const { outcome, copy } = useCopy();
  return (
    <div
      role="alert"
      aria-label={labels.label}
      data-testid="error-row"
      className="flex flex-col gap-2 rounded-panel border border-danger/40 bg-sunken px-3 py-2.5"
    >
      <p className="flex items-start gap-2 text-ui text-danger">
        <TriangleExclamationPoint aria-hidden className="mt-0.5 size-4 shrink-0" />
        <span>{message}</span>
      </p>
      <div className="flex items-center gap-1">
        <ActionBarPrimitive.Reload asChild>
          <Button variant="outline" data-testid="retry-error">
            <RotateCcw aria-hidden className="size-4" />
            {labels.retry}
          </Button>
        </ActionBarPrimitive.Reload>
        <Button onClick={() => copy(message)} data-testid="copy-error">
          <Copy aria-hidden className="size-4" />
          <span role="status" aria-live="polite">
            {outcome === "copied" ? labels.copied : labels.copyError}
          </span>
        </Button>
      </div>
    </div>
  );
}
