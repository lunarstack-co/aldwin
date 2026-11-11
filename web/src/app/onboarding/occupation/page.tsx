"use client";

import { useState } from "react";

import { TextField } from "@/components/molecules/text-field";
import { ProfileStep } from "@/components/organisms/profile-step";
import { ROUTES } from "@/constants/routes";
import copy from "@/data/onboarding/occupation.json";
import { useSetup } from "@/hooks/use-setup";
import { saveProfile } from "@/utils/api";

export default function OccupationPage() {
  const { status } = useSetup();
  const [occupation, setOccupation] = useState(status.profile.occupation);
  return (
    <ProfileStep
      step={2}
      title={copy.title}
      lede={copy.lede}
      back={ROUTES.name}
      next={ROUTES.personality}
      onContinue={() => saveProfile({ occupation })}
    >
      <TextField
        id="occupation"
        label={copy.label}
        autoComplete="organization-title"
        maxLength={200}
        value={occupation}
        onChange={(event) => setOccupation(event.target.value)}
        autoFocus
      />
    </ProfileStep>
  );
}
