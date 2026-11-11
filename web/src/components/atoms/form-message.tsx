import type { ReactNode } from "react";

/** Progress text, read out politely by screen readers. */
export function FormStatus({ children }: { children: ReactNode }) {
  return (
    <p role="status" aria-live="polite" className="min-h-5 text-ui text-fg-secondary">
      {children}
    </p>
  );
}

/** An error, read out straight away by screen readers. */
export function FormAlert({ children }: { children: ReactNode }) {
  return (
    <p role="alert" aria-live="assertive" className="min-h-5 text-ui text-danger">
      {children}
    </p>
  );
}
