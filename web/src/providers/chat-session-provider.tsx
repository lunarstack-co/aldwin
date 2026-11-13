"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { ChatSession } from "@/utils/ws/session";

const ChatSessionContext = createContext<ChatSession | null>(null);

export function ChatSessionProvider({ session, children }: { session: ChatSession; children: ReactNode }) {
  return <ChatSessionContext.Provider value={session}>{children}</ChatSessionContext.Provider>;
}

export function useSession(): ChatSession {
  const session = useContext(ChatSessionContext);
  if (session === null) throw new Error("useSession needs a <ChatSessionProvider> above it.");
  return session;
}
