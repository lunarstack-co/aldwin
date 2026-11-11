import type { HTMLAttributes } from "react";

import { cn } from "@/utils/cn";

const WIDTHS = {
  narrow: "max-w-[480px]",
  form: "max-w-[520px]",
  wide: "max-w-[680px]",
} as const;

/** A single centred column that fills the page height. */
export function CenteredLayout({
  width,
  className,
  ...props
}: HTMLAttributes<HTMLElement> & { width: keyof typeof WIDTHS }) {
  return (
    <section
      className={cn(
        "mx-auto flex w-full flex-1 flex-col justify-center px-4 py-16 sm:px-6",
        WIDTHS[width],
        className,
      )}
      {...props}
    />
  );
}
