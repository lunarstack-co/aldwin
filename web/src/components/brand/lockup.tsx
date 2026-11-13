import Link from "next/link";

import copy from "@/data/app.json";
import { cn } from "@/utils/cn";

import { Mark } from "./mark";

export function Lockup({ className, onNavigate }: { className?: string; onNavigate?: () => void }) {
  return (
    <Link
      href="/"
      aria-label={copy.brandLink}
      onClick={onNavigate}
      className={cn(
        "text-foreground focus-visible:ring-ring/50 inline-flex items-center gap-2 rounded-md px-1 py-1 outline-none focus-visible:ring-2",
        className,
      )}
    >
      <Mark />
      <span className="font-display text-sm font-[550]">{copy.brand}</span>
    </Link>
  );
}
