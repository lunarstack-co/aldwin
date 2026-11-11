"use client";

import { ProfileStep } from "@/components/organisms/profile-step";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ROUTES } from "@/constants/routes";
import copy from "@/data/onboarding/occupation.json";
import { useSetup } from "@/hooks/use-setup";
import { useProfileAnswer } from "@/state/onboarding-store";
import { saveProfile } from "@/utils/api";

export default function OccupationPage() {
  const { status } = useSetup();
  const [occupation, setOccupation] = useProfileAnswer("occupation", status.profile.occupation);
  return (
    <ProfileStep
      step={2}
      title={copy.title}
      lede={copy.lede}
      back={ROUTES.name}
      next={ROUTES.personality}
      onContinue={() => saveProfile({ occupation: occupation })}
    >
      <Field id="occupation" label={copy.label}>
        <Input
          id="occupation"
          autoComplete="organization-title"
          maxLength={200}
          value={occupation}
          onChange={(event) => setOccupation(event.target.value)}
          autoFocus
          className="max-md:h-10"
        />
      </Field>
    </ProfileStep>
  );
}
