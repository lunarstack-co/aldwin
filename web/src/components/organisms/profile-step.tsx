"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { FormEvent, ReactNode } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { ROUTES } from "@/constants/routes";
import copy from "@/data/onboarding/profile-step.json";
import { useAction } from "@/hooks/use-action";
import { useSetup } from "@/hooks/use-setup";
import { saveProfile } from "@/utils/api";
import { fill } from "@/utils/text";

import { FormAlert } from "./form-message";
import { StepHeader } from "./step-header";

const PROFILE_STEPS = 3;

/**
 * One page of the optional profile step. `onContinue` saves this page's
 * answer; Skip is always there, marks the whole step as skipped, and never
 * holds Aldwin up.
 */
export function ProfileStep({
  step,
  title,
  lede,
  back,
  next,
  continueLabel = copy.continue,
  onContinue,
  children,
}: {
  step: number;
  title: string;
  lede: string;
  back?: string;
  next: string;
  continueLabel?: string;
  onContinue: () => Promise<unknown>;
  children: ReactNode;
}) {
  const router = useRouter();
  const { refresh } = useSetup();
  const { busy, problem, run } = useAction();

  function saveThenGo(save: () => Promise<unknown>, to: string) {
    void run(async () => {
      await save();
      await refresh();
      router.push(to);
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    saveThenGo(onContinue, next);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-6" aria-busy={busy}>
      <StepHeader eyebrow={fill(copy.eyebrow, { step, total: PROFILE_STEPS })} title={title}>
        {lede}
      </StepHeader>
      {children}
      <FormAlert>{problem}</FormAlert>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={busy} className="max-md:h-10">
          {continueLabel}
        </Button>
        {back ? (
          <Link href={back} className={buttonVariants({ variant: "outline", className: "max-md:h-10" })}>
            {copy.back}
          </Link>
        ) : null}
        <Button
          variant="ghost"
          disabled={busy}
          className="ml-auto max-md:h-10"
          onClick={() => saveThenGo(() => saveProfile({ skipped: true }), ROUTES.home)}
        >
          {copy.skip}
        </Button>
      </div>
    </form>
  );
}
