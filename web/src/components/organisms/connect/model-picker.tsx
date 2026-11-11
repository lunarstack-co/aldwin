"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/atoms/button";
import { SelectField } from "@/components/molecules/select-field";
import copy from "@/data/onboarding/model-picker.json";
import type { ModelChoice } from "@/types/setup";

export function ModelPicker({
  busy,
  choices,
  onChoose,
}: {
  busy: boolean;
  choices: ModelChoice[];
  onChoose: (ref: string) => void;
}) {
  const [chosen, setChosen] = useState(choices[0]?.ref ?? "");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onChoose(chosen);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <SelectField
        id="model"
        label={copy.label}
        value={chosen}
        options={choices.map((c) => ({ value: c.ref, label: c.label }))}
        onChange={setChosen}
      />
      <Button
        type="submit"
        variant="primary"
        disabled={busy || chosen === ""}
        className="self-start"
      >
        {copy.submit}
      </Button>
    </form>
  );
}
