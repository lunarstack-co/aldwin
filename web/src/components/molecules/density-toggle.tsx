import { cn } from "@/utils/cn";
import type { Density } from "@/utils/density";

export type DensityLabels = { label: string; clean: string; detail: string };

/** Clean keeps tool calls behind a chip; Detail shows them as they happen. */
export function DensityToggle({
  value,
  onChange,
  labels,
}: {
  value: Density;
  onChange: (density: Density) => void;
  labels: DensityLabels;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={labels.label}
      data-testid="density-toggle"
      className="inline-flex rounded-control border border-border-default bg-control p-0.5 text-xs font-medium"
    >
      {(["clean", "detail"] as const).map((id) => {
        const checked = value === id;
        return (
          <button
            key={id}
            type="button"
            role="radio"
            aria-checked={checked}
            onClick={() => onChange(id)}
            className={cn(
              "h-7 cursor-pointer rounded-[4px] px-2.5 text-fg-secondary transition-[background-color,color] duration-[80ms]",
              checked ? "bg-raised text-fg shadow-[0_1px_0_0_#1f23280a]" : "hover:text-fg",
            )}
          >
            {labels[id]}
          </button>
        );
      })}
    </div>
  );
}
