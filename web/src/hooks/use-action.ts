"use client";

import { useCallback, useState } from "react";

import { messageOf } from "@/utils/errors";

/** One action at a time; `run` resolves to whether it succeeded. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const run = useCallback(async (action: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setProblem(null);
    try {
      await action();
      return true;
    } catch (error) {
      setProblem(messageOf(error));
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const clear = useCallback(() => setProblem(null), []);

  return { busy, problem, run, clear };
}
