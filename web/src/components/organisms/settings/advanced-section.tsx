"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Field, hintId } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import copy from "@/data/settings/advanced.json";
import common from "@/data/settings/common.json";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { LIMIT_FIELDS, type LimitField, type Limits, type SettingsView } from "@/types/settings";
import { ApiError, getSettings, patchSettings } from "@/utils/api";
import { messageOf } from "@/utils/errors";
import { changedLimits } from "@/utils/settings";

// The host names the field it refused, as in `limits.max_steps: …`.
function refusedField(message: string): LimitField | null {
  return LIMIT_FIELDS.find((field) => message.startsWith(`limits.${field}:`)) ?? null;
}

/** Saved to the same settings the terminal reads. */
export function AdvancedSection() {
  const [saved, setSaved] = useState<SettingsView | null>(null);
  const [limits, setLimits] = useState<Limits | null>(null);
  const [updates, setUpdates] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [problem, setProblem] = useState<{
    field: LimitField | null;
    message: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    getSettings().then(
      (view) => {
        setSaved(view);
        setLimits(view.limits);
        setUpdates(view.check_for_updates);
      },
      (error: unknown) => setLoadError(messageOf(error)),
    );
  }, []);

  const edited =
    saved !== null &&
    limits !== null &&
    (Object.keys(changedLimits(saved.limits, limits)).length > 0 || updates !== saved.check_for_updates);
  useUnsavedChanges(edited);

  if (loadError !== null)
    return (
      <Callout type="error" title={copy.loadError}>
        {loadError}
      </Callout>
    );
  if (saved === null || limits === null) return <p className="text-muted-foreground text-sm">{common.loading}</p>;

  const changed = changedLimits(saved.limits, limits);
  const updatesChanged = updates !== saved.check_for_updates;
  const dirty = Object.keys(changed).length > 0 || updatesChanged;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    setDone(false);
    try {
      const view = await patchSettings({
        ...(Object.keys(changed).length > 0 ? { limits: changed } : {}),
        ...(updatesChanged ? { check_for_updates: updates } : {}),
      });
      setSaved(view);
      setLimits(view.limits);
      setUpdates(view.check_for_updates);
      setDone(true);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : messageOf(error);
      setProblem({ field: refusedField(message), message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="flex flex-col gap-6" data-testid="advanced-form">
      <fieldset className="grid gap-4 @lg:grid-cols-2">
        <legend className="mb-3 text-sm font-medium">{copy.limitsLabel}</legend>
        {LIMIT_FIELDS.map((field) => {
          const id = `limit-${field}`;
          const text = copy.limits[field];
          const refused = problem?.field === field;
          return (
            <Field
              key={field}
              id={id}
              label={text.label}
              hint={text.hint}
              error={refused ? problem.message : undefined}
            >
              <Input
                id={id}
                type="number"
                inputMode="numeric"
                step={1}
                value={Number.isNaN(limits[field]) ? "" : limits[field]}
                onChange={(event) => setLimits({ ...limits, [field]: event.target.valueAsNumber })}
                aria-describedby={hintId(id, text.hint, refused ? problem.message : undefined)}
                aria-invalid={refused || undefined}
                data-testid={id}
                className="max-md:h-10"
              />
            </Field>
          );
        })}
      </fieldset>
      <label className="flex items-start gap-3 text-sm">
        <Switch checked={updates} onCheckedChange={setUpdates} data-testid="check-for-updates" />
        <span className="flex flex-col">
          <span className="font-medium">{copy.updates}</span>
          <span className="text-muted-foreground">{copy.updatesHint}</span>
        </span>
      </label>
      {problem !== null && problem.field === null ? (
        <p role="alert" className="text-destructive text-sm">
          {problem.message}
        </p>
      ) : null}
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={!dirty || busy} data-testid="advanced-save" className="max-md:h-10">
          {busy ? common.saving : common.save}
        </Button>
        {dirty ? (
          <Button
            type="button"
            variant="ghost"
            onClick={() => {
              setLimits(saved.limits);
              setUpdates(saved.check_for_updates);
              setProblem(null);
            }}
            data-testid="advanced-cancel"
            className="max-md:h-10"
          >
            {common.cancel}
          </Button>
        ) : null}
        {done && !dirty ? (
          <span role="status" className="text-muted-foreground text-sm">
            {common.saved}
          </span>
        ) : null}
      </div>
    </form>
  );
}
