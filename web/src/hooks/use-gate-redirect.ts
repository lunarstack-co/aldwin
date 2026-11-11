"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import type { SetupStatus } from "@/types/setup";
import { gateTarget } from "@/utils/gates";

/** Moves the page to where the gates say it belongs; returns that target, or `null` to stay. */
export function useGateRedirect(status: SetupStatus | null): string | null {
  const pathname = usePathname();
  const router = useRouter();
  const target = status === null ? null : gateTarget(status, pathname);
  useEffect(() => {
    if (target !== null) router.replace(target);
  }, [target, router]);
  return target;
}
