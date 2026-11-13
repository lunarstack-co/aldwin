"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import copy from "@/data/onboarding/key-form.json";
import type { ProviderOption } from "@/types/setup";
import { listKeyProviders } from "@/utils/api";

import { SecretInput } from "../secret-input";

const FALLBACK_PROVIDERS: ProviderOption[] = [copy.fallbackProvider];

/** The key field clears after each try. */
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

  const items = providers.map((p) => ({ value: p.name, label: p.title }));
  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field id="provider" label={copy.provider}>
        <Select items={items} value={provider} onValueChange={(value) => value && setProvider(String(value))}>
          <SelectTrigger id="provider" className="w-full max-md:h-10" data-testid="provider-select">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {items.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Field>
      <Field id="api-key" label={copy.apiKey}>
        <SecretInput id="api-key" value={key} onChange={(event) => setKey(event.target.value)} required />
      </Field>
      <Button type="submit" disabled={busy || key.trim() === ""} className="self-start max-md:h-10">
        {copy.submit}
      </Button>
    </form>
  );
}
