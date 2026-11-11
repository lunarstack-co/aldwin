import type { SelectHTMLAttributes } from "react";

import { inputClass } from "@/components/atoms/input";
import { cn } from "@/utils/cn";

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(inputClass, className)} {...props} />;
}
