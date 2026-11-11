import type { ReactNode } from "react";

import copy from "@/data/app.json";

/** The skip link and the main landmark every page sits in. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-control focus:bg-fg focus:px-3 focus:py-1.5 focus:text-ui focus:font-medium focus:text-fg-inverse"
      >
        {copy.skipLink}
      </a>
      <main id="main" className="flex flex-1 flex-col">
        {children}
      </main>
    </>
  );
}
