import type { ReactNode } from "react";

/** The post-setup frame: a fixed rail on the left and a fluid stage beside it. */
export function ChatLayout({
  rail,
  railLabel,
  stageLabel,
  children,
}: {
  rail: ReactNode;
  railLabel: string;
  stageLabel: string;
  children: ReactNode;
}) {
  return (
    <div data-testid="aldwin-chat-shell" className="flex min-h-0 flex-1">
      <nav
        aria-label={railLabel}
        className="hidden w-60 shrink-0 flex-col border-r border-border-subtle bg-sunken p-3 md:flex"
      >
        {rail}
      </nav>
      <section aria-label={stageLabel} className="relative flex min-w-0 flex-1 flex-col">
        {children}
      </section>
    </div>
  );
}
