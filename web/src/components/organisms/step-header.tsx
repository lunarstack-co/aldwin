import type { ReactNode } from "react";

/** Eyebrow, heading and lede at the top of a setup page. */
export function StepHeader({ eyebrow, title, children }: { eyebrow: string; title: string; children: ReactNode }) {
  return (
    <header className="flex flex-col gap-2">
      {/* Phones show the step position above the page instead, so one counter is enough. */}
      <p className="text-muted-foreground hidden text-sm md:block">{eyebrow}</p>
      <h1 className="text-2xl">{title}</h1>
      <p className="text-muted-foreground text-base">{children}</p>
    </header>
  );
}
