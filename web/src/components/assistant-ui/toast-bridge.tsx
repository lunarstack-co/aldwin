"use client";

import { useEffect } from "react";
import { toast } from "sonner";

import { useChatStore } from "@/state/chat-store";

export const NOTICE_TOAST_MS = 6000;

/** The socket layer only queues notices; this shows them. */
export function ToastBridge() {
  const toasts = useChatStore((s) => s.toasts);
  const dropToast = useChatStore((s) => s.dropToast);
  useEffect(() => {
    for (const notice of toasts) {
      toast(notice.message, { id: notice.id, duration: NOTICE_TOAST_MS });
      dropToast(notice.id);
    }
  }, [toasts, dropToast]);
  return null;
}
