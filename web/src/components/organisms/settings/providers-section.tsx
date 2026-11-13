"use client";

import { useState, type FormEvent } from "react";

import { BrandMark } from "@/components/brand/brand-mark";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Field, hintId } from "@/components/ui/field";
import common from "@/data/settings/common.json";
import copy from "@/data/settings/providers.json";
import { useModels } from "@/hooks/use-settings-data";
import { useSettingsView } from "@/hooks/use-settings-view";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { useSession } from "@/providers/chat-session-provider";
import type { ProviderModels } from "@/types/protocol";
import type { KeyRow } from "@/types/settings";
import { ApiError, saveSecret } from "@/utils/api";
import { messageOf } from "@/utils/errors";
import { addServer, disconnectProvider, loadModels } from "@/utils/settings/actions";
import { fill } from "@/utils/text";

import { ModelPicker } from "../chat/model-picker";
import { ServerForm } from "../connect/server-form";
import { SecretInput } from "../secret-input";
import { byTone, StatusPill, type Tone } from "./status";

/** Green when it works, amber when something set up has stopped working, grey when not set up. */
function toneOf(provider: ProviderModels): Tone {
  if (provider.status === "ok") return "ok";
  return provider.configured ? "attention" : "off";
}

const LOOPBACK = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i;

/** The host checks the key with the provider before saving it. */
function KeyEntry({
  provider,
  onSaved,
  onCancel,
}: {
  provider: ProviderModels;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [key, setKey] = useState("");
  useUnsavedChanges(key.trim() !== "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{
    message: string;
    retry: boolean;
  } | null>(null);
  const id = `key-${provider.name}`;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      await saveSecret("provider", provider.name, key.trim());
      setKey("");
      onSaved();
    } catch (error) {
      // An unreachable provider may answer next time; a refused key needs a different key.
      const retry = error instanceof ApiError && error.status === 502;
      setProblem({ message: messageOf(error), retry });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2" data-testid={`key-form-${provider.name}`}>
      <Field
        id={id}
        label={fill(copy.keyLabel, { title: provider.title })}
        hint={fill(copy.keyHint, { title: provider.title })}
        error={problem?.message}
      >
        <SecretInput
          id={id}
          value={key}
          onChange={(event) => setKey(event.target.value)}
          autoComplete="off"
          aria-describedby={hintId(id, copy.keyHint, problem?.message)}
          aria-invalid={problem !== null || undefined}
          required
        />
      </Field>
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={busy || key.trim() === ""} className="max-md:h-10">
          {busy ? common.checking : problem?.retry ? common.retry : copy.saveKey}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} className="max-md:h-10">
          {common.cancel}
        </Button>
      </div>
    </form>
  );
}

function ProviderRow({
  provider,
  keyRow,
  onChanged,
}: {
  provider: ProviderModels;
  keyRow: KeyRow | undefined;
  onChanged: () => void;
}) {
  const session = useSession();
  const [editing, setEditing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const hasKey = keyRow !== undefined && keyRow.masked !== "";

  async function disconnect() {
    setProblem(null);
    const outcome = await disconnectProvider(session, provider.name);
    if (outcome.ok) onChanged();
    else setProblem(outcome.message);
  }

  return (
    <li
      className="flex flex-col gap-3 rounded-lg border p-3"
      data-provider={provider.name}
      data-status={provider.status}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <BrandMark name={provider.name} kind="provider" className="size-5" />
        <span className="font-medium">{provider.title}</span>
        <StatusPill tone={toneOf(provider)}>{copy.status[provider.status]}</StatusPill>
        {provider.local ? <span className="text-muted-foreground text-xs">{copy.local}</span> : null}
        <span className="ms-auto flex gap-1">
          {!provider.local && !editing ? (
            <Button variant="ghost" size="sm" onClick={() => setEditing(true)} className="max-md:h-10">
              {hasKey ? copy.replaceKey : copy.addKey}
            </Button>
          ) : null}
          {provider.configured ? (
            <Button variant="ghost" size="sm" onClick={disconnect} className="max-md:h-10">
              {copy.disconnect}
            </Button>
          ) : null}
        </span>
      </div>
      {keyRow?.source === "env" ? <p className="text-muted-foreground text-xs">{copy.fromEnv}</p> : null}
      {editing ? (
        <KeyEntry
          provider={provider}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
          onCancel={() => setEditing(false)}
        />
      ) : null}
      {problem !== null ? (
        <p role="alert" className="text-destructive text-sm">
          {problem}
        </p>
      ) : null}
    </li>
  );
}

export function ProvidersSection() {
  const session = useSession();
  const models = useModels();
  const { view, reload } = useSettingsView();
  const [serverBusy, setServerBusy] = useState(false);
  const [serverProblem, setServerProblem] = useState<string | null>(null);
  const [addingServer, setAddingServer] = useState(false);

  // Keys and statuses both change after a save, so both are read again.
  function refresh() {
    reload();
    session.refreshModels(true);
  }

  async function connect(name: string, address: string, key: string) {
    setServerBusy(true);
    setServerProblem(null);
    const outcome = await addServer(session, {
      name,
      baseUrl: address,
      key,
      local: LOOPBACK.test(address),
    });
    setServerBusy(false);
    if (outcome.ok) {
      setAddingServer(false);
      refresh();
    } else setServerProblem(outcome.message);
  }

  if (models.status === "error") {
    return (
      <Callout type="error" title={copy.loadError}>
        <Button variant="outline" size="sm" onClick={() => void loadModels(session)}>
          {common.retry}
        </Button>
      </Callout>
    );
  }

  const providers = models.data.providers;
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">{copy.current}</h3>
        <p className="text-muted-foreground text-sm">{copy.currentHint}</p>
        <ModelPicker placement="settings" />
      </div>
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">{copy.providers}</h3>
        {models.status !== "ready" ? (
          <p className="text-muted-foreground text-sm">{common.loading}</p>
        ) : providers.length === 0 ? (
          <p className="text-muted-foreground text-sm">{copy.empty}</p>
        ) : (
          <ul className="flex flex-col gap-2" data-testid="provider-list">
            {byTone(providers, toneOf).map((provider) => (
              <ProviderRow
                key={provider.name}
                provider={provider}
                keyRow={view?.keys.find((row) => row.kind === "provider" && row.name === provider.name)}
                onChanged={refresh}
              />
            ))}
          </ul>
        )}
      </div>
      {addingServer ? (
        <section
          aria-labelledby="add-server-title"
          className="flex flex-col gap-3 rounded-lg border p-3"
          data-testid="add-server"
        >
          <h3 id="add-server-title" className="text-sm font-medium">
            {copy.addServer}
          </h3>
          <p className="text-muted-foreground text-sm">{copy.addServerLede}</p>
          <ServerForm
            busy={serverBusy}
            onSubmit={connect}
            onCancel={() => {
              setAddingServer(false);
              setServerProblem(null);
            }}
          />
          {serverProblem !== null ? (
            <p role="alert" className="text-destructive text-sm">
              {serverProblem}
            </p>
          ) : null}
        </section>
      ) : (
        <Button
          variant="outline"
          onClick={() => setAddingServer(true)}
          data-testid="add-server-open"
          className="self-start max-md:h-10"
        >
          {copy.addServer}
        </Button>
      )}
    </div>
  );
}
