import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { buttonClass } from "@/components/atoms/button";

/** A button that stays pressed while selected, with a tick to show it. */
export function ToggleChip({
  pressed,
  onToggle,
  children,
}: {
  pressed: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onToggle}
      className={buttonClass({ variant: pressed ? "primary" : "outline" })}
    >
      {pressed ? <Check aria-hidden className="size-4" /> : null}
      {children}
    </button>
  );
}
