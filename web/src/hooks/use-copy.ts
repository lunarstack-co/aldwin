"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { COPY_FEEDBACK_MS } from "@/constants/chat";

export type CopyOutcome = "idle" | "copied" | "failed";

/** Puts text on the clipboard and reports the result for a moment. */
export function useCopy() {
  const [outcome, setOutcome] = useState<CopyOutcome>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );

  const copy = useCallback(async (text: string) => {
    let next: CopyOutcome = "copied";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      next = "failed";
    }
    setOutcome(next);
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => setOutcome("idle"), COPY_FEEDBACK_MS);
  }, []);

  return { outcome, copy };
}
