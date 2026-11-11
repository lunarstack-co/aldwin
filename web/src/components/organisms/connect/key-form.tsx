"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/atoms/button";
import { SelectField } from "@/components/molecules/select-field";
import { TextField } from "@/components/molecules/text-field";
import copy from "@/data/onboarding/key-form.json";
import type { ProviderOption } from "@/types/setup";
import { listKeyProviders } from "@/utils/api";

const FALLBACK_PROVIDERS: ProviderOption[] = [copy.fallbackProvider];

/** A cloud provider and its API key; the key field clears after each try. */
export function KeyForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (provider: string, key: string) => void;
}) {
  const [providers, setProviders] = useState(FALLBACK_PROVIDERS);
  const [provider, setProvider] = useState(copy.fallbackProvider.name);
  const [key, setKey] = useState("");

  useEffect(() => {
    listKeyProviders()
      .then((rows) => {
        if (rows.length > 0) setProviders(rows);
      })
      .catch(() => undefined);
  }, []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(provider, key.trim());
    setKey("");
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <SelectField
        id="provider"
        label={copy.provider}
        value={provider}
        options={providers.map((p) => ({ value: p.name, label: p.title }))}
        onChange={setProvider}
      />
      <TextField
        id="api-key"
        label={copy.apiKey}
        type="password"
        autoComplete="off"
        spellCheck={false}
        value={key}
        onChange={(event) => setKey(event.target.value)}
        required
      />
      <Button
        type="submit"
        variant="primary"
        disabled={busy || key.trim() === ""}
        className="self-start"
      >
        {copy.submit}
      </Button>
    </form>
  );
}
