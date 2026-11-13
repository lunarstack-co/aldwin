"use client";

import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import common from "@/data/settings/common.json";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import copy from "@/data/settings/sign-in.json";
import { useSession } from "@/providers/chat-session-provider";
import { useSettingsStore, type SignIn } from "@/state/settings-store";
import { cancelSignIn, pasteSignIn, tokenSignIn } from "@/utils/settings/actions";
import { fill } from "@/utils/text";

import { SecretInput } from "../secret-input";

function Answer({ signIn }: { signIn: SignIn }) {
  const session = useSession();
  const [value, setValue] = useState("");
  useUnsavedChanges(value.trim() !== "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const token = signIn.mode === "token";
  const id = `sign-in-${signIn.name}`;
  const label = token ? fill(copy.tokenLabel, { title: signIn.title }) : copy.pasteLabel;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    const answer = value.trim();
    // The token is a secret: it leaves the field as soon as it is sent.
    if (token) setValue("");
    const outcome = token
      ? await tokenSignIn(session, signIn.name, answer)
      : await pasteSignIn(session, signIn.name, answer);
    setBusy(false);
    if (!outcome.ok) setProblem(outcome.message);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2">
      <Field id={id} label={label} error={problem ?? undefined}>
        {token ? (
          <SecretInput
            id={id}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoComplete="off"
            required
          />
        ) : (
          <Input
            id={id}
            type="url"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            required
            className="max-md:h-10"
          />
        )}
      </Field>
      <Button type="submit" size="sm" disabled={busy || value.trim() === ""} className="self-start max-md:h-10">
        {busy ? common.checking : copy.submit}
      </Button>
    </form>
  );
}

/** The sign-in page opens only on a click; the panel closes when the host's time runs out. */
export function ConnectorSignIn({ onExpired }: { onExpired: (title: string) => void }) {
  const session = useSession();
  const signIn = useSettingsStore((s) => s.signIn);

  useEffect(() => {
    if (signIn === null) return;
    const timer = window.setTimeout(
      () => {
        useSettingsStore.getState().setSignIn(null);
        onExpired(signIn.title);
      },
      Math.max(0, signIn.expiresAt - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [signIn, onExpired]);

  if (signIn === null) return null;
  return (
    <section
      aria-labelledby="sign-in-title"
      data-testid="connector-sign-in"
      data-mode={signIn.mode}
      className="border-live/40 flex flex-col gap-3 rounded-lg border p-3"
    >
      <h3 id="sign-in-title" className="text-sm font-medium">
        {fill(copy.title, { title: signIn.title })}
      </h3>
      <p className="text-muted-foreground text-sm">
        {signIn.mode === "browser"
          ? copy.browser
          : signIn.mode === "paste"
            ? copy.paste
            : fill(copy.token, { title: signIn.title })}
      </p>
      {signIn.url !== null && signIn.mode !== "token" ? (
        <a
          href={signIn.url}
          target="_blank"
          rel="noreferrer noopener"
          data-testid="sign-in-open"
          className="self-start text-sm font-medium underline underline-offset-4"
        >
          {copy.open}
        </a>
      ) : null}
      {signIn.mode === "token" && signIn.tokenHelpUrl !== null ? (
        <a
          href={signIn.tokenHelpUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="self-start text-sm underline underline-offset-4"
        >
          {copy.tokenHelp}
        </a>
      ) : null}
      {signIn.mode === "browser" ? (
        <p role="status" className="text-muted-foreground text-sm">
          {copy.waiting}
        </p>
      ) : (
        <Answer key={`${signIn.name}-${signIn.mode}`} signIn={signIn} />
      )}
      <Button
        variant="ghost"
        size="sm"
        onClick={() => cancelSignIn(session)}
        data-testid="sign-in-cancel"
        className="self-start max-md:h-10"
      >
        {copy.cancel}
      </Button>
    </section>
  );
}
