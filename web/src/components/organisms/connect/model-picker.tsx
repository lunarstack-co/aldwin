"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Field } from "@/components/ui/field";
import copy from "@/data/onboarding/model-picker.json";
import type { ModelChoice } from "@/types/setup";

/** Pick the model to use; typing filters the list. */
export function ModelPicker({
  busy,
  choices,
  onChoose,
}: {
  busy: boolean;
  choices: ModelChoice[];
  onChoose: (ref: string) => void;
}) {
  const [chosen, setChosen] = useState<string | null>(choices[0]?.ref ?? null);
  const labels = new Map(choices.map((c) => [c.ref, c.label]));

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (chosen) onChoose(chosen);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Field id="model" label={copy.label}>
        <Combobox
          items={choices.map((c) => c.ref)}
          value={chosen}
          onValueChange={(value) => setChosen(value)}
          itemToStringLabel={(ref: string) => labels.get(ref) ?? ref}
        >
          <ComboboxInput id="model" placeholder={copy.search} className="max-md:[&_input]:h-10" data-testid="model-combobox" />
          <ComboboxContent>
            <ComboboxEmpty>{copy.none}</ComboboxEmpty>
            <ComboboxList>
              {(ref: string) => (
                <ComboboxItem key={ref} value={ref}>
                  {labels.get(ref) ?? ref}
                </ComboboxItem>
              )}
            </ComboboxList>
          </ComboboxContent>
        </Combobox>
      </Field>
      <Button type="submit" disabled={busy || !chosen} className="self-start max-md:h-10">
        {copy.submit}
      </Button>
    </form>
  );
}
