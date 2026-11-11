import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/utils/cn";

import { Label } from "./label";

/** A label above its control, with an optional hint the control is described by. */
export function Field({
  id,
  label,
  hint,
  className,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("flex w-full flex-col gap-1.5", className)}>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="text-muted-foreground text-xs">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export function hintId(id: string, hint: string | undefined): ComponentProps<"input">["aria-describedby"] {
  return hint ? `${id}-hint` : undefined;
}
