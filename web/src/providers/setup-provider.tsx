"use client";

import { createContext, useCallback, useEffect, useState, type ReactNode } from "react";

import { Mark } from "@/components/atoms/mark";
import copy from "@/data/app.json";
import { useGateRedirect } from "@/hooks/use-gate-redirect";
import { CenteredLayout } from "@/layouts/centered-layout";
import type { SetupStatus } from "@/types/setup";
import { getSetupStatus } from "@/utils/api";
import { messageOf } from "@/utils/errors";

export type SetupContextValue = {
  status: SetupStatus;
  refresh: () => Promise<SetupStatus>;
};

export const SetupContext = createContext<SetupContextValue | null>(null);

/**
 * Loads the setup status and sends the page where setup says it belongs,
 * before showing it. There is no sign-in page: the session cookie comes from
 * the link `aldwin web` prints.
 */
export function SetupProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const next = await getSetupStatus();
    setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    getSetupStatus().then(setStatus, (error: unknown) => setProblem(messageOf(error)));
  }, []);

  const target = useGateRedirect(status);

  if (problem !== null) {
    return (
      <CenteredLayout width="narrow" className="gap-2">
        <h1 className="text-h2 font-semibold">{copy.brand}</h1>
        <p role="alert" className="text-md text-fg-secondary">
          {problem}
        </p>
      </CenteredLayout>
    );
  }
  if (status === null || target !== null) {
    return (
      <div
        role="status"
        aria-busy="true"
        data-testid="aldwin-gates-loading"
        className="flex flex-1 flex-col items-center justify-center gap-3"
      >
        <Mark className="size-10" />
        <p className="text-ui text-fg-secondary">{copy.loading}</p>
      </div>
    );
  }
  return <SetupContext.Provider value={{ status, refresh }}>{children}</SetupContext.Provider>;
}
