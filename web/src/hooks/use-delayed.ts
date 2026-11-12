"use client";

import { useEffect, useState } from "react";

/** False until `ms` have passed since mount, then true. */
export function useDelayed(ms: number): boolean {
  const [done, setDone] = useState(false);
  useEffect(() => {
    const timer = window.setTimeout(() => setDone(true), ms);
    return () => window.clearTimeout(timer);
  }, [ms]);
  return done;
}
