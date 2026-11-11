"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/atoms/button";
import { TextField } from "@/components/molecules/text-field";
import copy from "@/data/onboarding/server-form.json";

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
      <TextField
        id="server-name"
        label={copy.name}
        hint={copy.nameHint}
        value={name}
        onChange={(event) => setName(event.target.value)}
        required
      />
      <TextField
        id="server-address"
        label={copy.address}
        hint={copy.addressHint}
        type="url"
        placeholder={copy.addressPlaceholder}
        value={address}
        onChange={(event) => setAddress(event.target.value)}
        required
      />
      <TextField
        id="server-key"
        label={copy.apiKey}
        type="password"
        autoComplete="off"
        spellCheck={false}
        value={key}
        onChange={(event) => setKey(event.target.value)}
      />
      <Button type="submit" variant="primary" disabled={busy} className="self-start">
        {copy.submit}
      </Button>
    </form>
  );
}
