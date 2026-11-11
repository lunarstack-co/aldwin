"use client";

import { ProfileStep } from "@/components/organisms/profile-step";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ROUTES } from "@/constants/routes";
import copy from "@/data/onboarding/name.json";
import { useSetup } from "@/hooks/use-setup";
import { useProfileAnswer } from "@/state/onboarding-store";
import { saveProfile } from "@/utils/api";

export default function NamePage() {
  const { status } = useSetup();
  const [firstName, setFirstName] = useProfileAnswer("first_name", status.profile.first_name);
  return (
    <ProfileStep
      step={1}
      title={copy.title}
      lede={copy.lede}
      next={ROUTES.occupation}
      onContinue={() => saveProfile({ first_name: firstName })}
    >
      <Field id="first-name" label={copy.label}>
        <Input
          id="first-name"
          autoComplete="given-name"
          maxLength={200}
          value={firstName}
          onChange={(event) => setFirstName(event.target.value)}
          autoFocus
          className="max-md:h-10"
        />
      </Field>
    </ProfileStep>
  );
}
