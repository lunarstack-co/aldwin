import type { ReactNode } from "react";

import { OnboardingLayout } from "@/layouts/onboarding-layout";

export default function Layout({ children }: { children: ReactNode }) {
  return <OnboardingLayout>{children}</OnboardingLayout>;
}
