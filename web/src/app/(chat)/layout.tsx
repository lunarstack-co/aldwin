import { Suspense, type ReactNode } from "react";

import { ChatShell } from "@/components/organisms/chat/chat-shell";

/**
 * `/` and `/c/` share this layout, so moving between conversations keeps one
 * chat session and one socket instead of starting the screen again.
 */
export default function ChatGroupLayout({ children }: { children: ReactNode }) {
  return (
    <div data-testid="aldwin-gates-clear" className="flex min-h-0 flex-1 flex-col">
      <Suspense fallback={null}>
        <ChatShell>{children}</ChatShell>
      </Suspense>
    </div>
  );
}
