"use client";

import { X } from "lucide-react";

import { Button } from "@/components/atoms/button";
import copy from "@/data/chat/history-notice.json";
import { useChatStore } from "@/state/chat-store";

/** Why switching conversations did not happen; the current one stays as it was. */
export function SessionNoticeBanner() {
  const notice = useChatStore((s) => s.notice);
  const setNotice = useChatStore((s) => s.setNotice);
  if (notice === null) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="session-notice"
      data-notice={notice.code}
      className="flex items-center justify-between gap-3 border-b border-border-subtle bg-sunken px-4 py-2 text-ui text-fg-secondary"
    >
      <span>
        {notice.code === "busy" ? copy.busy : notice.code === "not_found" ? copy.notFound : notice.message}
      </span>
      <Button icon onClick={() => setNotice(null)} aria-label={copy.dismiss} title={copy.dismiss}>
        <X aria-hidden className="size-4" />
      </Button>
    </div>
  );
}
