"use client";

import { useCallback } from "react";

import { HOST_READY_POLL } from "@/constants/connect";
import copy from "@/data/onboarding/connect.json";
import { useSetup } from "@/hooks/use-setup";
import { getSetupStatus } from "@/utils/api";
import { wait } from "@/utils/time";

/** Resolves to false when keys still do not work. */
export function useHostReady(): () => Promise<boolean> {
  const { refresh } = useSetup();
  return useCallback(async () => {
    for (let tries = 0; tries < HOST_READY_POLL.tries; tries += 1) {
      const status = await getSetupStatus();
      if (!status.keys_ok) return false;
      if (status.host_ready) {
        await refresh();
        return true;
      }
      await wait(HOST_READY_POLL.intervalMs);
    }
    throw new Error(copy.messages.notStarted);
  }, [refresh]);
}
