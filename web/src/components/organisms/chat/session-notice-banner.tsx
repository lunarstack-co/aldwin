"use client";

import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import copy from "@/data/chat/history-notice.json";
import { useChatStore } from "@/state/chat-store";

/** Why switching conversations did not happen; the current one stays as it was. */
export function SessionNoticeBanner() {
  const notice = useChatStore((s) => s.notice);
  const setNotice = useChatStore((s) => s.setNotice);
  if (notice === null) return null;
  const text = notice.code === "busy" ? copy.busy : notice.code === "not_found" ? copy.notFound : notice.message;
  return (
    <Callout
      type="warning"
      title={copy.label}
      role="status"
      aria-live="polite"
      data-testid="session-notice"
      data-notice={notice.code}
      className="relative mx-4 my-2 pe-12"
    >
      <p>{text}</p>
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => setNotice(null)}
        aria-label={copy.dismiss}
        title={copy.dismiss}
        className="absolute top-2 right-2 max-md:size-10"
      >
        <X aria-hidden />
      </Button>
    </Callout>
  );
}

/** The conversation was saved mid-answer, so its last turn has no ending. */
export function InterruptedNotice() {
  const interrupted = useChatStore((s) => s.interrupted && s.turns.length > 0);
  if (!interrupted) return null;
  return (
    <Callout
      type="warning"
      title={copy.interruptedLabel}
      role="status"
      data-testid="history-interrupted"
      className="mx-4 my-2"
    >
      <p>{copy.interrupted}</p>
    </Callout>
  );
}
