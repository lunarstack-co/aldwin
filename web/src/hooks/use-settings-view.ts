"use client";

import { useCallback, useEffect, useState } from "react";

import type { SettingsView } from "@/types/settings";
import { getSettings } from "@/utils/api";
import { messageOf } from "@/utils/errors";

/** `GET /v1/settings`: the saved settings with keys masked, and a way to read them again after a change. */
export function useSettingsView(): { view: SettingsView | null; error: string | null; reload: () => void } {
  const [view, setView] = useState<SettingsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    getSettings().then(
      (next) => {
        setView(next);
        setError(null);
      },
      (problem: unknown) => setError(messageOf(problem)),
    );
  }, []);
  useEffect(reload, [reload]);
  return { view, error, reload };
}
