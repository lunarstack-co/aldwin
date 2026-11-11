"use client";

import { useEffect, useState } from "react";

import { createChatSession, type ChatSession } from "@/utils/ws/session";

/** One chat session for the life of the screen; the socket opens on mount and closes on unmount. */
export function useChatSession(): ChatSession {
  const [session] = useState(() => createChatSession());
  useEffect(() => {
    session.start();
    return () => session.dispose();
  }, [session]);
  return session;
}
