import type { ReactNode } from "react";

import { ToastBridge } from "@/components/assistant-ui/toast-bridge";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import copy from "@/data/app.json";

/** The skip link, the main landmark every page sits in, and the toasts above it all. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <TooltipProvider>
      <a
        href="#main"
        className="bg-foreground text-background sr-only rounded-md px-3 py-1.5 text-sm font-medium focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50"
      >
        {copy.skipLink}
      </a>
      <main id="main" className="flex min-h-0 flex-1 flex-col">
        {children}
      </main>
      <Toaster position="bottom-center" closeButton />
      <ToastBridge />
    </TooltipProvider>
  );
}
