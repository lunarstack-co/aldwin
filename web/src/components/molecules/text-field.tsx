import type { InputHTMLAttributes } from "react";

import { Input } from "@/components/atoms/input";
import { Label } from "@/components/atoms/label";

export function TextField({
  label,
  hint,
  id,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: string; id: string; hint?: string }) {
  return (
    <div className="flex w-full flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} aria-describedby={hint ? `${id}-hint` : undefined} {...props} />
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-fg-secondary">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
