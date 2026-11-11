"use client";

import { useState } from "react";

import { AnswerStylePicker } from "@/components/organisms/answer-style-picker";
import { ProfileStep } from "@/components/organisms/profile-step";
import { ROUTES } from "@/constants/routes";
import copy from "@/data/onboarding/personality.json";
import { useSetup } from "@/hooks/use-setup";
import { saveProfile } from "@/utils/api";

export default function PersonalityPage() {
  const { status } = useSetup();
  const [chosen, setChosen] = useState<string[]>(status.profile.personality);
  return (
    <ProfileStep
      step={3}
      title={copy.title}
      lede={copy.lede}
      back={ROUTES.occupation}
      next={ROUTES.home}
      continueLabel={copy.finish}
      onContinue={() => saveProfile({ personality: chosen, complete: true })}
    >
      <AnswerStylePicker chosen={chosen} onChange={setChosen} />
    </ProfileStep>
  );
}
