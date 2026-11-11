import { create } from "zustand";

import type { ProfileAnswers } from "@/types/setup";

type OnboardingState = {
  /** What the person has typed or picked on each profile page, saved or not. */
  drafts: Partial<ProfileAnswers>;
  setDraft: <K extends keyof ProfileAnswers>(key: K, value: ProfileAnswers[K]) => void;
};

/**
 * Keeps profile answers while the person moves between pages, so Back and the
 * browser's back button never wipe a page. The host still only hears an
 * answer on Continue.
 */
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
