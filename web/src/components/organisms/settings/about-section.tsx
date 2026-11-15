"use client";

import { useEffect, useState } from "react";

import { Callout } from "@/components/ui/callout";
import { Switch } from "@/components/ui/switch";
import { SAVED_MS } from "@/constants/settings";
import copy from "@/data/settings/about.json";
import common from "@/data/settings/common.json";
import type { HomeInfo } from "@/types/settings";
import { ApiError, getHomeInfo, getSettings, patchSettings } from "@/utils/api";
import { messageOf } from "@/utils/errors";

/** The update check costs nothing, so the switch saves as soon as it moves. */
export function AboutSection() {
  const [info, setInfo] = useState<HomeInfo | null>(null);
  const [updates, setUpdates] = useState<boolean | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getHomeInfo(), getSettings()]).then(
      ([home, view]) => {
        setInfo(home);
        setUpdates(view.check_for_updates);
      },
      (failure: unknown) => setLoadError(messageOf(failure)),
    );
  }, []);

  useEffect(() => {
    if (!saved) return;
    const timer = window.setTimeout(() => setSaved(false), SAVED_MS);
    return () => window.clearTimeout(timer);
  }, [saved]);

  async function toggle(next: boolean) {
    setUpdates(next);
    setBusy(true);
    setSaved(false);
    setError(null);
    try {
      const view = await patchSettings({ check_for_updates: next });
      setUpdates(view.check_for_updates);
      setSaved(true);
    } catch (failure) {
      setUpdates(!next);
      setError(failure instanceof ApiError ? failure.message : messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  if (loadError !== null)
    return (
      <Callout type="error" title={copy.loadError}>
        {loadError}
      </Callout>
    );
  if (info === null || updates === null) return <p className="text-muted-foreground text-sm">{common.loading}</p>;

  return (
    <div className="flex max-w-[45rem] flex-col gap-6" data-testid="about">
      <dl className="flex flex-col gap-1 text-sm">
        <dt className="font-medium">{copy.version}</dt>
        <dd className="text-muted-foreground tabular-nums" data-testid="about-version">
          {info.version}
        </dd>
      </dl>
      <div className="flex flex-col gap-2">
        <label className="flex min-h-11 items-start gap-3 text-sm">
          <Switch
            checked={updates}
            onCheckedChange={(next) => void toggle(next)}
            disabled={busy}
            data-testid="check-for-updates"
            className="mt-0.5"
          />
          <span className="flex flex-col">
            <span className="font-medium">{copy.updates}</span>
            <span className="text-muted-foreground">{copy.updatesHint}</span>
          </span>
        </label>
        {saved ? (
          <span role="status" className="text-muted-foreground text-sm">
            {common.saved}
          </span>
        ) : null}
        {error ? (
          <p role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
}
