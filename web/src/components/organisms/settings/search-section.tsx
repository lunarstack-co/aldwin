"use client";

import { useState, type FormEvent } from "react";

import { BrandMark } from "@/components/brand/brand-mark";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Field, hintId } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import common from "@/data/settings/common.json";
import copy from "@/data/settings/search.json";
import { useSearchProviders } from "@/hooks/use-settings-data";
import { useSettingsView } from "@/hooks/use-settings-view";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { useSession } from "@/providers/chat-session-provider";
import type { SearchProviderItem } from "@/types/protocol";
import { patchSettings } from "@/utils/api";
import { messageOf } from "@/utils/errors";
import { connectSearch, disconnectSearch, loadSearch, makeSearchActive } from "@/utils/settings/actions";
import { fill } from "@/utils/text";

import { SecretInput } from "../secret-input";
import { byTone, StatusPill } from "./status";

/** The host checks the key with the engine before saving it. */
function KeyEntry({ item, onDone }: { item: SearchProviderItem; onDone: () => void }) {
  const session = useSession();
  const [key, setKey] = useState("");
  useUnsavedChanges(key.trim() !== "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const id = `search-key-${item.name}`;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    const outcome = await connectSearch(session, item.name, key.trim());
    setBusy(false);
    if (outcome.ok) {
      setKey("");
      onDone();
    } else setProblem(outcome.message);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2" data-testid={`search-key-form-${item.name}`}>
      <Field
        id={id}
        label={fill(copy.keyLabel, { title: item.title })}
        hint={fill(copy.keyHint, { title: item.title })}
        error={problem ?? undefined}
      >
        <SecretInput
          id={id}
          value={key}
          onChange={(event) => setKey(event.target.value)}
          autoComplete="off"
          aria-describedby={hintId(id, copy.keyHint, problem ?? undefined)}
          aria-invalid={problem !== null || undefined}
          required
        />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" size="sm" disabled={busy || key.trim() === ""} className="max-md:h-10">
          {busy ? common.checking : copy.saveKey}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onDone} className="max-md:h-10">
          {common.cancel}
        </Button>
        <a
          href={item.signup_url}
          target="_blank"
          rel="noreferrer noopener"
          className="text-sm underline underline-offset-4"
        >
          {copy.getKey}
        </a>
      </div>
    </form>
  );
}

/** SearXNG runs wherever the user put it; its address is a setting, not a key. */
function SearxngAddress() {
  const { view, reload } = useSettingsView();
  const [draft, setDraft] = useState<string | null>(null);
  const [state, setState] = useState<{
    busy: boolean;
    problem: string | null;
    saved: boolean;
  }>({ busy: false, problem: null, saved: false });
  const saved = view?.search.searxng_url ?? "";
  const value = draft ?? saved;
  const id = "searxng-url";
  useUnsavedChanges(draft !== null && draft.trim() !== saved);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setState({ busy: true, problem: null, saved: false });
    try {
      await patchSettings({ search: { searxng_url: value.trim() } });
      setDraft(null);
      reload();
      setState({ busy: false, problem: null, saved: true });
    } catch (error) {
      setState({ busy: false, problem: messageOf(error), saved: false });
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2" data-testid="searxng-form">
      <Field id={id} label={copy.searxngUrl} hint={copy.searxngHint} error={state.problem ?? undefined}>
        <Input
          id={id}
          type="url"
          value={value}
          onChange={(event) => setDraft(event.target.value)}
          aria-describedby={hintId(id, copy.searxngHint, state.problem ?? undefined)}
          aria-invalid={state.problem !== null || undefined}
          required
          className="max-md:h-10"
        />
      </Field>
      <div className="flex items-center gap-3">
        <Button
          type="submit"
          size="sm"
          variant="outline"
          disabled={state.busy || draft === null}
          className="max-md:h-10"
        >
          {state.busy ? common.saving : copy.saveUrl}
        </Button>
        {draft !== null ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setDraft(null);
              setState({ busy: false, problem: null, saved: false });
            }}
            className="max-md:h-10"
          >
            {common.cancel}
          </Button>
        ) : null}
        {state.saved ? (
          <span role="status" className="text-muted-foreground text-sm">
            {common.saved}
          </span>
        ) : null}
      </div>
    </form>
  );
}

function EngineRow({ item }: { item: SearchProviderItem }) {
  const session = useSession();
  const [entering, setEntering] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const local = item.kind === "local";

  async function run(action: () => ReturnType<typeof makeSearchActive>) {
    setProblem(null);
    const outcome = await action();
    if (!outcome.ok) setProblem(outcome.message);
  }

  return (
    <li
      className="flex flex-col gap-3 rounded-lg border p-3"
      data-engine={item.name}
      data-active={item.active || undefined}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <BrandMark name={item.name} kind="search" className="size-5" />
        <span className="font-medium">{item.title}</span>
        <StatusPill tone={item.connected ? "ok" : "off"}>
          {item.active ? copy.active : item.connected ? copy.connected : copy.notConnected}
        </StatusPill>
        {local ? <span className="text-muted-foreground text-xs">{copy.local}</span> : null}
        {local && item.detail !== "" ? (
          <span
            className="text-muted-foreground min-w-0 truncate font-mono text-xs"
            data-testid={`search-detail-${item.name}`}
          >
            {item.detail}
          </span>
        ) : null}
        <span className="ms-auto flex flex-wrap gap-1">
          {!item.connected && !local && !entering ? (
            <Button variant="ghost" size="sm" onClick={() => setEntering(true)} className="max-md:h-10">
              {copy.connect}
            </Button>
          ) : null}
          {!item.connected && local ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void run(() => connectSearch(session, item.name))}
              className="max-md:h-10"
            >
              {copy.connect}
            </Button>
          ) : null}
          {item.connected && !item.active ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void run(() => makeSearchActive(session, item.name))}
              className="max-md:h-10"
            >
              {copy.use}
            </Button>
          ) : null}
          {item.connected ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void run(() => disconnectSearch(session, item.name))}
              className="max-md:h-10"
            >
              {copy.disconnect}
            </Button>
          ) : null}
        </span>
      </div>
      {item.check_note !== null ? <p className="text-muted-foreground text-xs">{item.check_note}</p> : null}
      {entering && !item.connected ? <KeyEntry item={item} onDone={() => setEntering(false)} /> : null}
      {item.name === "searxng" ? <SearxngAddress /> : null}
      {problem !== null ? (
        <p role="alert" className="text-destructive text-sm">
          {problem}
        </p>
      ) : null}
    </li>
  );
}

export function SearchSection() {
  const session = useSession();
  const search = useSearchProviders();

  if (search.status === "error") {
    return (
      <Callout type="error" title={copy.loadError}>
        <Button variant="outline" size="sm" onClick={() => void loadSearch(session)}>
          {common.retry}
        </Button>
      </Callout>
    );
  }
  if (search.status !== "ready") return <p className="text-muted-foreground text-sm">{common.loading}</p>;
  if (search.data.items.length === 0) return <p className="text-muted-foreground text-sm">{copy.empty}</p>;
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">{copy.engines}</h3>
        <ul className="flex flex-col gap-2" data-testid="search-list">
          {byTone(search.data.items, (item) => (item.active ? "ok" : item.connected ? "attention" : "off")).map(
            (item) => (
              <EngineRow key={item.name} item={item} />
            ),
          )}
        </ul>
      </div>
    </div>
  );
}
