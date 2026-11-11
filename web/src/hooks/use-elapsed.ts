"use client";

import { useEffect, useState } from "react";

import { WORK_TICK_MS } from "@/constants/chat";

/** Whole seconds since `startedAt`, ticking while `running`, frozen at `endedAt` after. */
export function useElapsed(startedAt: number, endedAt: number | null, running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), WORK_TICK_MS);
    return () => clearInterval(id);
  }, [running]);
  const end = running ? now : (endedAt ?? now);
  return Math.max(0, Math.round((end - startedAt) / 1000));
}
