"use client";

import { useEffect, useId, useRef } from "react";

import { Button } from "@/components/atoms/button";
import copy from "@/data/chat/take-over.json";

/** Another tab holds the socket; this one can take it. */
export function TakeOverDialog({ onTakeOver }: { onTakeOver: () => void }) {
  const titleId = useId();
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    button.current?.focus();
  }, []);
  return (
    <div className="absolute inset-0 z-40 flex items-center justify-center bg-base/70 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="take-over"
        className="flex w-full max-w-[420px] flex-col gap-4 rounded-overlay border border-border-default bg-raised p-6 shadow-dialog"
      >
        <h2 id={titleId} className="text-h3 font-semibold">
          {copy.title}
        </h2>
        <p className="text-md text-fg-secondary">{copy.body}</p>
        <Button
          ref={button}
          variant="primary"
          onClick={onTakeOver}
          className="self-end"
          data-testid="take-over-button"
        >
          {copy.takeOver}
        </Button>
      </div>
    </div>
  );
}
