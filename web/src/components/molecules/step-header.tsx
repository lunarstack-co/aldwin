import type { ReactNode } from "react";

/** Eyebrow, heading and lede at the top of a setup page. */
export function StepHeader({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-2">
      <p className="eyebrow">{eyebrow}</p>
      <h1 className="text-h2 font-semibold">{title}</h1>
      <p className="text-md text-fg-secondary">{children}</p>
    </header>
  );
}
