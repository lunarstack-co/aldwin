"use client";

import { useState } from "react";

import { ConnectPathPicker } from "@/components/organisms/connect/connect-path-picker";
import { KeyForm } from "@/components/organisms/connect/key-form";
import { LocalFinder } from "@/components/organisms/connect/local-finder";
import { ModelPicker } from "@/components/organisms/connect/model-picker";
import { ServerForm } from "@/components/organisms/connect/server-form";
import { FormAlert, FormStatus } from "@/components/organisms/form-message";
import { StepHeader } from "@/components/organisms/step-header";
import type { ConnectPath } from "@/constants/connect";
import copy from "@/data/onboarding/connect.json";
import { useAction } from "@/hooks/use-action";
import { useHostReady } from "@/hooks/use-host-ready";
import type { ModelChoice } from "@/types/setup";
import {
  chooseModel,
  connectServer,
  detectLocal,
  listModels,
  saveProviderKey,
} from "@/utils/api";
import { fill } from "@/utils/text";

const { messages } = copy;

export default function ConnectPage() {
  const { busy, problem, run, clear } = useAction();
  const waitForHost = useHostReady();
  const [path, setPath] = useState<ConnectPath>("key");
  const [progress, setProgress] = useState<string | null>(null);
  const [choices, setChoices] = useState<ModelChoice[] | null>(null);

  async function attempt(action: () => Promise<void>) {
    if (!(await run(action))) setProgress(null);
  }

  /** Lets the gates move on once Aldwin runs; false while keys still do not work. */
  async function finishIfReady(): Promise<boolean> {
    setProgress(messages.starting);
    const ready = await waitForHost();
    if (!ready) setProgress(null);
    return ready;
  }

  async function offerModels(provider: string) {
    const models = await listModels(provider);
    if (models.length === 0) throw new Error(messages.noModels);
    setChoices(models.map((m) => ({ ref: `${provider}/${m.id}`, label: m.title || m.id })));
  }

  function pick(next: ConnectPath) {
    setPath(next);
    setChoices(null);
    clear();
  }

  function form() {
    if (choices !== null) {
      return (
        <ModelPicker
          busy={busy}
          choices={choices}
          onChoose={(ref) =>
            attempt(async () => {
              await chooseModel(ref);
              if (!(await finishIfReady())) throw new Error(messages.stillNoKey);
            })
          }
        />
      );
    }
    if (path === "key") {
      return (
        <KeyForm
          busy={busy}
          onSubmit={(provider, key) =>
            attempt(async () => {
              await saveProviderKey(provider, key);
              if (!(await finishIfReady())) await offerModels(provider);
            })
          }
        />
      );
    }
    if (path === "local") {
      return (
        <LocalFinder
          busy={busy}
          onFind={() =>
            attempt(async () => {
              const running = (await detectLocal()).filter((r) => r.running);
              const found = running.flatMap((r) =>
                r.models.map((m) => ({ ref: `${r.name}/${m.id}`, label: fill(copy.localModel, { model: m.title, runtime: r.title }) })),
              );
              if (found.length === 0) throw new Error(messages.noLocalModels);
              setChoices(found);
            })
          }
        />
      );
    }
    return (
      <ServerForm
        busy={busy}
        onSubmit={(name, address, key) =>
          attempt(async () => {
            await connectServer(name, address, key || undefined);
            await offerModels(name);
          })
        }
      />
    );
  }

  return (
    <div className="flex flex-col gap-6" aria-busy={busy}>
      <StepHeader eyebrow={copy.eyebrow} title={copy.title}>
        {copy.lede}
      </StepHeader>
      <ConnectPathPicker value={path} onChange={pick} />
      {form()}
      <FormStatus>{progress}</FormStatus>
      <FormAlert>{problem}</FormAlert>
    </div>
  );
}
