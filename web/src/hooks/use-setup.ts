"use client";

import { useContext } from "react";

import { SetupContext, type SetupContextValue } from "@/providers/setup-provider";

/** The current setup status and a way to re-read it; only inside `SetupProvider`. */
export function useSetup(): SetupContextValue {
  const context = useContext(SetupContext);
  if (context === null) throw new Error("useSetup needs a <SetupProvider> above it.");
  return context;
}
