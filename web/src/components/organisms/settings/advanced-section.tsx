"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { LIMIT_GROUPS, PERCENT_STEP, SAVED_MS } from "@/constants/settings";
import copy from "@/data/settings/advanced.json";
import common from "@/data/settings/common.json";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { LIMIT_FIELDS, type LimitField, type LimitRule, type Limits, type SettingsView } from "@/types/settings";
import { ApiError, getSettings, patchSettings } from "@/utils/api";
import { messageOf } from "@/utils/errors";
import { changedLimits, limitIsValid, withUnit } from "@/utils/settings";
import { fill } from "@/utils/text";

import { SaveBar } from "./save-bar";
import { SettingRow } from "./setting-row";

// The host names the field it refused, as in `limits.max_steps: …`.
function refusedField(message: string): LimitField | null {
  return LIMIT_FIELDS.find((field) => message.startsWith(`limits.${field}:`)) ?? null;
}

function defaultsOf(rules: Record<LimitField, LimitRule>): Partial<Limits> {
  return Object.fromEntries(LIMIT_FIELDS.map((field) => [field, rules[field].default]));
}

export function AdvancedSection() {
  const [saved, setSaved] = useState<SettingsView | null>(null);
  const [draft, setDraft] = useState<Limits | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refused, setRefused] = useState<{ field: LimitField | null; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [justSaved, setJustSaved] = useState(false);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    getSettings().then(
      (view) => {
        setSaved(view);
        setDraft(view.limits);
      },
      (error: unknown) => setLoadError(messageOf(error)),
    );
  }, []);

  useEffect(() => {
    if (!justSaved) return;
    const timer = window.setTimeout(() => setJustSaved(false), SAVED_MS);
    return () => window.clearTimeout(timer);
  }, [justSaved]);

  const changed = saved && draft ? changedLimits(saved.limits, draft) : {};
  const dirty = Object.keys(changed).length > 0;
  useUnsavedChanges(dirty);

  if (loadError !== null)
    return (
      <Callout type="error" title={copy.loadError}>
        {loadError}
      </Callout>
    );
  if (saved === null || draft === null) return <p className="text-muted-foreground text-sm">{common.loading}</p>;

  const rules = saved.limit_rules;
  const invalid = (field: LimitField) => field in changed && !limitIsValid(draft[field], rules[field]);
  const anyInvalid = LIMIT_FIELDS.some(invalid);

  function edit(field: LimitField, value: number) {
    if (draft === null) return;
    setDraft({ ...draft, [field]: value });
    setJustSaved(false);
    if (refused?.field === field || refused?.field === null) setRefused(null);
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dirty || anyInvalid || busy) return;
    setBusy(true);
    setRefused(null);
    try {
      const view = await patchSettings({ limits: changed });
      setSaved(view);
      setDraft(view.limits);
      setJustSaved(true);
    } catch (error) {
      const message = error instanceof ApiError ? error.message : messageOf(error);
      setRefused({ field: refusedField(message), message });
    } finally {
      setBusy(false);
    }
  }

  function row(field: LimitField) {
    if (saved === null || draft === null) return null;
    const id = `limit-${field}`;
    const text = copy.limits[field];
    const rule = rules[field];
    const value = draft[field];
    const ids = { label: `${id}-label`, description: `${id}-hint`, unit: `${id}-unit`, error: `${id}-error`, note: `${id}-note` };
    const description = `${text.hint} ${fill(copy.default, { value: withUnit(rule.default, text.unit) })}`;
    const error = invalid(field)
      ? fill(copy.invalid, { min: rule.min.toLocaleString("en"), max: rule.max.toLocaleString("en") })
      : refused?.field === field
        ? refused.message
        : null;
    const fromFile = !(field in changed) && saved.limits[field] > rule.max;
    const describedBy = [ids.description, error ? ids.error : null, fromFile ? ids.note : null].filter(Boolean).join(" ");

    const below = (
      <>
        {error ? (
          <p id={ids.error} role="alert" className="text-destructive text-sm">
            {error}
          </p>
        ) : null}
        {fromFile ? (
          <p id={ids.note} className="text-muted-foreground text-xs">
            {fill(copy.fromFile, { value: withUnit(saved.limits[field], text.unit), max: withUnit(rule.max, text.unit) })}
          </p>
        ) : null}
      </>
    );

    if (field === "context_compact_percent") {
      return (
        <SettingRow
          key={field}
          testId={`row-${field}`}
          label={text.label}
          labelId={ids.label}
          description={description}
          descriptionId={ids.description}
          below={below}
          control={
            <div className="flex items-center gap-3">
              <Slider
                value={value}
                min={rule.min}
                max={rule.max}
                step={PERCENT_STEP}
                onValueChange={(next) => edit(field, next)}
                thumb={{
                  "aria-labelledby": ids.label,
                  "aria-describedby": describedBy,
                  getAriaValueText: (_formatted, next) => withUnit(next, text.unit),
                }}
                className="flex-1"
                data-testid={id}
              />
              <span aria-hidden="true" data-testid={`${id}-value`} className="w-11 shrink-0 text-right text-sm tabular-nums">
                {withUnit(value, text.unit)}
              </span>
            </div>
          }
        />
      );
    }

    return (
      <SettingRow
        key={field}
        testId={`row-${field}`}
        label={text.label}
        labelId={ids.label}
        labelFor={id}
        description={description}
        descriptionId={ids.description}
        below={below}
        control={
          <div className="flex items-center gap-2">
            <Input
              id={id}
              type="number"
              inputMode="numeric"
              min={rule.min}
              max={rule.max}
              step={1}
              value={Number.isNaN(value) ? "" : value}
              onChange={(event) => edit(field, event.target.valueAsNumber)}
              aria-describedby={`${ids.unit} ${describedBy}`}
              aria-invalid={error ? true : undefined}
              data-testid={id}
              className="min-w-0 flex-1 max-md:h-11"
            />
            {/* A fixed-width unit slot keeps the boxes lined up. */}
            <span id={ids.unit} data-testid={ids.unit} className="text-muted-foreground w-20 shrink-0 text-sm">
              {text.unit}
            </span>
          </div>
        }
      />
    );
  }

  return (
    <form noValidate onSubmit={save} className="flex flex-1 flex-col" data-testid="advanced-form">
      <div className="flex max-w-[45rem] flex-col gap-6 pb-6">
        {LIMIT_GROUPS.map((group) => (
          <section key={group.id} aria-labelledby={`limits-${group.id}`} data-testid={`group-${group.id}`}>
            <h3 id={`limits-${group.id}`} className="text-base font-medium">
              {copy.groups[group.id].title}
            </h3>
            <div className="divide-border divide-y">{group.fields.map(row)}</div>
          </section>
        ))}
        <div>
          <Button
            type="button"
            variant="outline"
            onClick={() => setResetting(true)}
            data-testid="advanced-reset"
            className="max-md:h-11"
          >
            {copy.reset.open}
          </Button>
        </div>
      </div>
      {dirty || justSaved ? (
        <SaveBar
          saved={justSaved && !dirty}
          busy={busy}
          canSave={dirty && !anyInvalid}
          needsFix={anyInvalid}
          error={refused?.message ?? null}
          onDiscard={() => {
            setDraft(saved.limits);
            setRefused(null);
          }}
        />
      ) : null}
      <Dialog open={resetting} onOpenChange={setResetting}>
        <DialogContent data-testid="reset-dialog" showCloseButton={false}>
          <DialogTitle>{copy.reset.title}</DialogTitle>
          <DialogDescription>{copy.reset.body}</DialogDescription>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setResetting(false)} className="max-md:h-11">
              {common.cancel}
            </Button>
            <Button
              type="button"
              onClick={() => {
                setDraft({ ...draft, ...defaultsOf(rules) });
                setJustSaved(false);
                setResetting(false);
              }}
              data-testid="reset-confirm"
              className="max-md:h-11"
            >
              {copy.reset.confirm}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </form>
  );
}
