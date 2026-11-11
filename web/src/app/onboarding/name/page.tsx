"use client";

import { useState } from "react";

import { TextField } from "@/components/molecules/text-field";
import { ProfileStep } from "@/components/organisms/profile-step";
import { ROUTES } from "@/constants/routes";
import copy from "@/data/onboarding/name.json";
import { useSetup } from "@/hooks/use-setup";
import { saveProfile } from "@/utils/api";

export default function NamePage() {
  const { status } = useSetup();
  const [firstName, setFirstName] = useState(status.profile.first_name);
  return (
    <ProfileStep
      step={1}
      title={copy.title}
      lede={copy.lede}
      next={ROUTES.occupation}
      onContinue={() => saveProfile({ first_name: firstName })}
    >
      <TextField
        id="first-name"
        label={copy.label}
        autoComplete="given-name"
        maxLength={200}
        value={firstName}
        onChange={(event) => setFirstName(event.target.value)}
        autoFocus
      />
    </ProfileStep>
  );
}
