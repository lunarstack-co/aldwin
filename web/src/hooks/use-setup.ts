"use client";

import { useContext } from "react";

import { SetupContext, type SetupContextValue } from "@/providers/setup-provider";

/** Only works inside `SetupProvider`. */
export function useSetup(): SetupContextValue {
  const context = useContext(SetupContext);
  if (context === null) throw new Error("useSetup needs a <SetupProvider> above it.");
  return context;
}
