"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Field, hintId } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import copy from "@/data/onboarding/server-form.json";

import { SecretInput } from "../secret-input";

/** An OpenAI-compatible server by name and address, with an optional key. */
export function ServerForm({
  busy,
  onSubmit,
}: {
  busy: boolean;
  onSubmit: (name: string, address: string, key: string) => void;
}) {
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [key, setKey] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(name.trim(), address.trim(), key.trim());
    setKey("");
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field id="server-name" label={copy.name} hint={copy.nameHint}>
        <Input
          id="server-name"
          aria-describedby={hintId("server-name", copy.nameHint)}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          className="max-md:h-10"
        />
      </Field>
      <Field id="server-address" label={copy.address} hint={copy.addressHint}>
        <Input
          id="server-address"
          type="url"
          aria-describedby={hintId("server-address", copy.addressHint)}
          placeholder={copy.addressPlaceholder}
          value={address}
          onChange={(event) => setAddress(event.target.value)}
          required
          className="max-md:h-10"
        />
      </Field>
      <Field id="server-key" label={copy.apiKey}>
        <SecretInput id="server-key" value={key} onChange={(event) => setKey(event.target.value)} />
      </Field>
      <Button type="submit" disabled={busy} className="self-start max-md:h-10">
        {copy.submit}
      </Button>
    </form>
  );
}
