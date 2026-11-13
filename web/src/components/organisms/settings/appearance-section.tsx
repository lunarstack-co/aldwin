"use client";

import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { DENSITIES } from "@/constants/chat";
import { THEMES } from "@/constants/theme";
import densityCopy from "@/data/chat/density-toggle.json";
import copy from "@/data/settings/appearance.json";
import themeCopy from "@/data/theme-toggle.json";
import { useDensity } from "@/hooks/use-density";
import { cn } from "@/utils/cn";
import { isDensity } from "@/utils/density";

const noop = () => () => {};
const OPTION =
  "has-data-checked:border-foreground/40 has-data-checked:bg-foreground/[0.04] flex min-h-11 cursor-pointer items-center gap-2.5 rounded-lg border px-3 py-2.5 text-sm font-normal";

/** Kept in this browser, like the header's view menu. */
export function AppearanceSection() {
  const { theme, setTheme } = useTheme();
  const [density, setDensity] = useDensity();
  // The prerendered page knows no theme; only the browser does.
  const mounted = useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );

  return (
    <div className="flex flex-col gap-6">
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">{themeCopy.label}</legend>
        <p className="text-muted-foreground mb-2 text-sm">{copy.themeHint}</p>
        <RadioGroup
          aria-label={themeCopy.label}
          value={mounted ? theme : undefined}
          onValueChange={(value) => setTheme(String(value))}
          className="grid gap-2 sm:grid-cols-3"
        >
          {THEMES.map((id) => (
            <Label key={id} htmlFor={`theme-${id}-option`} className={OPTION}>
              <RadioGroupItem id={`theme-${id}-option`} value={id} data-testid={`appearance-theme-${id}`} />
              {themeCopy[id]}
            </Label>
          ))}
        </RadioGroup>
      </fieldset>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">{densityCopy.label}</legend>
        <p className="text-muted-foreground mb-2 text-sm">{copy.densityHint}</p>
        <RadioGroup
          aria-label={densityCopy.label}
          value={density}
          onValueChange={(value) => {
            if (isDensity(value)) setDensity(value);
          }}
          className="grid gap-2 sm:grid-cols-2"
        >
          {DENSITIES.map((id) => (
            <Label key={id} htmlFor={`density-${id}-option`} className={cn(OPTION, "items-start")}>
              <RadioGroupItem
                id={`density-${id}-option`}
                value={id}
                aria-describedby={`density-${id}-hint`}
                data-testid={`appearance-density-${id}`}
                className="mt-0.5"
              />
              <span className="flex flex-col gap-0.5">
                <span className="font-medium">{densityCopy[id]}</span>
                <span id={`density-${id}-hint`} className="text-foreground/70">
                  {copy.density[id]}
                </span>
              </span>
            </Label>
          ))}
        </RadioGroup>
      </fieldset>
    </div>
  );
}
