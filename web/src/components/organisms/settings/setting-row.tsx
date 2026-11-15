import type { ReactNode } from "react";

import { Label } from "@/components/ui/label";

/** One setting beside its control, stacked when the section is narrow. */
export function SettingRow({
  label,
  labelId,
  labelFor,
  description,
  descriptionId,
  control,
  below,
  testId,
}: {
  label: string;
  labelId: string;
  /** The input the label names; a slider names itself with `aria-labelledby` instead. */
  labelFor?: string;
  description: string;
  descriptionId: string;
  control: ReactNode;
  below?: ReactNode;
  testId: string;
}) {
  return (
    <div data-testid={testId} className="flex flex-col gap-3 py-4 @md:flex-row @md:items-start @md:gap-6">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        {labelFor ? (
          <Label id={labelId} htmlFor={labelFor}>
            {label}
          </Label>
        ) : (
          <span id={labelId} className="text-sm leading-none font-medium">
            {label}
          </span>
        )}
        <p id={descriptionId} className="text-muted-foreground text-sm">
          {description}
        </p>
      </div>
      <div data-slot="setting-control" className="flex w-full min-w-0 flex-col gap-1.5 @md:w-52 @md:shrink-0">
        {control}
        {below}
      </div>
    </div>
  );
}
