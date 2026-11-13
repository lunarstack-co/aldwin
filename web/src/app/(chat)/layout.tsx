import { Suspense, type ReactNode } from "react";

import { ChatShell } from "@/components/organisms/chat/chat-shell";

/** Shared by `/` and `/c/`, so switching conversations keeps one session and socket. */
export default function ChatGroupLayout({ children }: { children: ReactNode }) {
  return (
    <div data-testid="aldwin-gates-clear" className="flex min-h-0 flex-1 flex-col">
      <Suspense fallback={null}>
        <ChatShell>{children}</ChatShell>
      </Suspense>
    </div>
  );
}
