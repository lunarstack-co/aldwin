import { create } from "zustand";

import type { ProfileAnswers } from "@/types/setup";

type OnboardingState = {
  drafts: Partial<ProfileAnswers>;
  setDraft: <K extends keyof ProfileAnswers>(key: K, value: ProfileAnswers[K]) => void;
};

/** Drafts survive Back; the host only hears an answer on Continue. */
export const useOnboardingStore = create<OnboardingState>((set) => ({
  drafts: {},
  setDraft: (key, value) => set((state) => ({ drafts: { ...state.drafts, [key]: value } })),
}));

/** One profile answer: the draft if there is one, else what the host has saved. */
export function useProfileAnswer<K extends keyof ProfileAnswers>(
  key: K,
  saved: ProfileAnswers[K],
): [ProfileAnswers[K], (value: ProfileAnswers[K]) => void] {
  const draft = useOnboardingStore((s) => s.drafts[key]) as ProfileAnswers[K] | undefined;
  const setDraft = useOnboardingStore((s) => s.setDraft);
  return [draft ?? saved, (value) => setDraft(key, value)];
}
