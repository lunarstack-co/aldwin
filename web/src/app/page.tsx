"use client";

import { ChatShell } from "@/components/organisms/chat/chat-shell";

export default function Home() {
  return (
    <div data-testid="aldwin-gates-clear" className="flex min-h-0 flex-1 flex-col">
      <ChatShell />
    </div>
  );
}
