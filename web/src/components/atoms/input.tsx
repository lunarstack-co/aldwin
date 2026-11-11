import type { InputHTMLAttributes } from "react";

import { cn } from "@/utils/cn";

/** Single-line control look, 32 px high to sit beside medium buttons. */
export const inputClass =
  "h-8 w-full rounded-control border border-border-default bg-control px-3 text-ui text-control-fg placeholder:text-fg-tertiary disabled:opacity-50";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(inputClass, className)} {...props} />;
}
