import type { ReactNode } from "react";

import { Callout } from "@/components/ui/callout";
import copy from "@/data/onboarding/connect.json";

/** Read out politely by screen readers. */
export function FormStatus({ children }: { children: ReactNode }) {
  return (
    <p role="status" aria-live="polite" className="text-muted-foreground min-h-5 text-sm">
      {children}
    </p>
  );
}

/** Read out at once; renders nothing without an error. */
export function FormAlert({ children }: { children: ReactNode }) {
  return (
    <div role="alert" aria-live="assertive">
      {children ? (
        <Callout type="error" title={copy.problem} className="my-0">
          <p>{children}</p>
        </Callout>
      ) : null}
    </div>
  );
}
