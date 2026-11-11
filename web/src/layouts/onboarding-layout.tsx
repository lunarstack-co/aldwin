import type { ReactNode } from "react";

import copy from "@/data/app.json";
import { CenteredLayout } from "@/layouts/centered-layout";

/** The frame around every first-run page. */
export function OnboardingLayout({ children }: { children: ReactNode }) {
  return (
    <CenteredLayout width="form" className="gap-8">
      <p className="brand-label">{copy.brand}</p>
      {children}
    </CenteredLayout>
  );
}
