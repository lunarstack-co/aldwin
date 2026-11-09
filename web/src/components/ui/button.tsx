import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export type ButtonVariant = "ghost" | "outline" | "primary";

// Medium buttons: 32 px high, with rest, hover and active states.
const BASE =
  "inline-flex h-8 cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-control text-ui font-medium transition-[background-color,color,border-color] duration-[80ms] ease-out disabled:pointer-events-none disabled:opacity-50";

const VARIANTS: Record<ButtonVariant, string> = {
  ghost: "px-3 text-fg-secondary hover:bg-neutral-muted hover:text-fg active:bg-control-active",
  outline:
    "border border-border-default bg-control px-3 text-control-fg shadow-[0_1px_0_0_#1f23280a] hover:bg-control-hover active:bg-control-active",
  primary:
    "border border-[#1f232826] bg-primary px-3 text-accent-fg hover:bg-primary-hover",
};

/**
 * Class recipe for anything that looks like a button, so links and real
 * buttons share one set of states.
 */
export function buttonClass({
  variant = "ghost",
  icon = false,
  className,
}: {
  variant?: ButtonVariant;
  icon?: boolean;
  className?: string;
} = {}): string {
  return cn(BASE, VARIANTS[variant], icon && "w-8 px-0", className);
}

export function Button({
  variant = "ghost",
  icon = false,
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  icon?: boolean;
}) {
  return (
    <button
      type={type}
      className={buttonClass({ variant, icon, className })}
      {...props}
    />
  );
}
