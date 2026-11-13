"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { Mark } from "@/components/brand/mark";
import { Step, Steps } from "@/components/ui/steps";
import { ROUTES } from "@/constants/routes";
import copy from "@/data/onboarding/steps.json";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";

const STEPS = [
  { id: "connect", path: ROUTES.connect, optional: false },
  { id: "name", path: ROUTES.name, optional: true },
  { id: "occupation", path: ROUTES.occupation, optional: true },
  { id: "personality", path: ROUTES.personality, optional: true },
] as const;

function withSlash(path: string): string {
  return path.endsWith("/") ? path : `${path}/`;
}

export function OnboardingLayout({ children, path: shownPath }: { children: ReactNode; path?: string }) {
  const current = usePathname();
  // The gate passes the page it is about to open, so the step list never jumps.
  const path = withSlash(shownPath ?? current);
  const index = Math.max(
    0,
    STEPS.findIndex((s) => s.path === path),
  );
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-1 flex-col gap-8 px-4 py-10 sm:px-6 md:flex-row md:gap-12 md:pt-[min(18vh,160px)] md:pb-16">
      <aside className="flex shrink-0 flex-col gap-6 md:w-52">
        <Mark className="size-8" />
        <p className="text-muted-foreground text-sm md:hidden" data-testid="step-position">
          {fill(copy.position, { step: index + 1, total: STEPS.length })} · {copy.steps[STEPS[index].id]}
        </p>
        <nav aria-label={copy.label} className="hidden md:block">
          <Steps data-testid="onboarding-steps">
            {STEPS.map((step, i) => (
              <Step key={step.id} aria-current={i === index ? "step" : undefined}>
                <p className={cn("text-sm", i === index ? "font-medium" : "text-muted-foreground")}>{copy.steps[step.id]}</p>
                <p className="text-muted-foreground text-xs">{step.optional ? copy.optional : copy.required}</p>
              </Step>
            ))}
          </Steps>
        </nav>
      </aside>
      <section className="flex min-w-0 flex-1 flex-col md:max-w-[520px]">{children}</section>
    </div>
  );
}
