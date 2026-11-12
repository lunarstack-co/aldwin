"use client";

import { usePathname } from "next/navigation";
import { createContext, useCallback, useEffect, useState, type ReactNode } from "react";

import { Mark } from "@/components/brand/mark";
import { Skeleton } from "@/components/ui/skeleton";
import { LOADING_DELAY_MS } from "@/constants/chat";
import copy from "@/data/app.json";
import { useDelayed } from "@/hooks/use-delayed";
import { useGateRedirect } from "@/hooks/use-gate-redirect";
import { CenteredLayout } from "@/layouts/centered-layout";
import { OnboardingLayout } from "@/layouts/onboarding-layout";
import type { SetupStatus } from "@/types/setup";
import { getSetupStatus } from "@/utils/api";
import { cn } from "@/utils/cn";
import { messageOf } from "@/utils/errors";

export type SetupContextValue = {
  status: SetupStatus;
  refresh: () => Promise<SetupStatus>;
};

export const SetupContext = createContext<SetupContextValue | null>(null);

/**
 * Loads the setup status and sends the page where setup says it belongs,
 * before showing it. There is no sign-in page: the session cookie comes from
 * the link `aldwin web` prints.
 */
export function SetupProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const next = await getSetupStatus();
    setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    getSetupStatus().then(setStatus, (error: unknown) => setProblem(messageOf(error)));
  }, []);

  const pathname = usePathname();
  const target = useGateRedirect(status);

  if (problem !== null) {
    return (
      <CenteredLayout width="narrow" className="gap-2">
        <h1 className="text-2xl">{copy.brand}</h1>
        <p role="alert" className="text-muted-foreground text-base">
          {problem}
        </p>
      </CenteredLayout>
    );
  }
  if (status === null || target !== null) {
    // Wait in the shape of the page that will open, so a reload never flashes the wrong frame.
    const opening = target ?? pathname;
    return opening.startsWith("/onboarding") ? <OnboardingLoading path={opening} /> : <GatesLoading />;
  }
  return (
    <SetupContext.Provider value={{ status, refresh }}>
      <FadeIn>{children}</FadeIn>
    </SetupContext.Provider>
  );
}

/**
 * Fades the page in once on load; moving between pages keeps it mounted. It
 * waits for the first painted frame, because a page still starting up can
 * spend a whole CSS animation before it draws anything.
 */
function FadeIn({ children }: { children: ReactNode }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setShown(true));
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, []);
  return (
    <div
      data-testid="page-fade"
      className={cn("flex min-h-0 flex-1 flex-col transition-opacity duration-300 ease-out", !shown && "opacity-0")}
    >
      {children}
    </div>
  );
}

/**
 * The shape of the chat while setup is checked, with the mark breathing in
 * the middle. It stays blank for the first moment, which is all a local host needs.
 */
function GatesLoading() {
  const shown = useDelayed(LOADING_DELAY_MS);
  return (
    <div role="status" aria-busy="true" data-testid="aldwin-gates-loading" data-shape="chat" className="relative flex min-h-0 flex-1">
      {shown ? <ChatPlaceholder /> : <span className="sr-only">{copy.loading}</span>}
    </div>
  );
}

function ChatPlaceholder() {
  return (
    <div data-testid="gates-placeholder" className="contents">
      <div className="hidden w-[260px] shrink-0 flex-col gap-2 border-r p-3 md:flex">
        <Skeleton className="h-7 w-24" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="mt-4 h-5 w-3/4" />
        <Skeleton className="h-5 w-2/3" />
      </div>
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-4">
        <Mark className="animate-live-pulse size-8" />
        <p className="text-muted-foreground text-sm">{copy.loading}</p>
        <Skeleton className="mt-6 h-24 w-full max-w-(--thread-max-width) rounded-thread" />
      </div>
    </div>
  );
}

/** The onboarding frame while setup is checked: the real step list, with bars where the page goes. */
function OnboardingLoading({ path }: { path: string }) {
  const shown = useDelayed(LOADING_DELAY_MS);
  return (
    <OnboardingLayout path={path}>
      <div role="status" aria-busy="true" data-testid="aldwin-gates-loading" data-shape="onboarding" className="flex flex-col gap-6">
        <span className="sr-only">{copy.loading}</span>
        {shown ? (
          <div data-testid="gates-placeholder" className="flex flex-col gap-6">
            <div className="flex flex-col gap-2">
              <Skeleton className="hidden h-4 w-32 md:block" />
              <Skeleton className="h-8 w-3/4" />
              <Skeleton className="h-5 w-full" />
            </div>
            <Skeleton className="h-10 w-full" />
          </div>
        ) : null}
      </div>
    </OnboardingLayout>
  );
}
