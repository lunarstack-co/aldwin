"use client";

import { Button } from "@/components/atoms/button";
import { RECONNECT } from "@/constants/chat";
import copy from "@/data/chat/reconnect.json";
import { useConnectionStore } from "@/state/connection-store";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";

/** Where the connection is, read out as it changes; silent while it is open. */
export function ReconnectBanner({ onRetry }: { onRetry: () => void }) {
  const phase = useConnectionStore((s) => s.phase);
  const attempt = useConnectionStore((s) => s.attempt);
  const text =
    phase === "connecting"
      ? copy.connecting
      : phase === "reconnecting"
        ? fill(copy.reconnecting, { attempt, limit: RECONNECT.attempts })
        : phase === "catching_up"
          ? copy.catchingUp
          : phase === "not_ready"
            ? copy.notReady
            : "";
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="connection-status"
      data-phase={phase}
      className={cn(
        "flex items-center justify-between gap-3 border-b border-border-subtle bg-sunken px-4 py-2 text-ui text-fg-secondary",
        text === "" && "sr-only",
      )}
    >
      <span>{text}</span>
      {phase === "not_ready" ? (
        <Button variant="outline" onClick={onRetry} data-testid="connection-retry">
          {copy.retry}
        </Button>
      ) : null}
    </div>
  );
}
