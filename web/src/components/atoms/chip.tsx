import type { HTMLAttributes } from "react";

import { cn } from "@/utils/cn";

/** Small pill for versions, models and tags. */
export function Chip({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn("chip", className)} {...props} />;
}
