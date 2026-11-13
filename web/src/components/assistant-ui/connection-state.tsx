"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { DotMatrix } from "@/components/ui/dot-matrix";
import { LOADING_DELAY_MS, RECONNECT } from "@/constants/chat";
import copy from "@/data/chat/reconnect.json";
import { useDelayed } from "@/hooks/use-delayed";
import { useConnectionStore, type ConnectionPhase } from "@/state/connection-store";
import { cn } from "@/utils/cn";
import { fill } from "@/utils/text";

const CONNECTED_FOR_MS = 2000;

function textOf(phase: ConnectionPhase, attempt: number): string {
  switch (phase) {
    case "connecting":
      return copy.connecting;
    case "reconnecting":
      return fill(copy.reconnecting, { attempt, limit: RECONNECT.attempts });
    case "catching_up":
      return copy.catchingUp;
    case "not_ready":
      return copy.waiting;
    case "open":
      return copy.connected;
    default:
      return "";
  }
}

/** "Connected" shows briefly after a drop; taken and stopped have their own screens. */
export function ConnectionState({ onRetry }: { onRetry: () => void }) {
  const phase = useConnectionStore((s) => s.phase);
  const attempt = useConnectionStore((s) => s.attempt);
  const [recovered, setRecovered] = useState(false);
  const [previous, setPrevious] = useState(phase);
  // A first connect that finishes quickly is not worth a banner.
  const patient = useDelayed(LOADING_DELAY_MS);

  if (phase !== previous) {
    setPrevious(phase);
    setRecovered(phase === "open" && previous !== "connecting");
  }

  useEffect(() => {
    if (!recovered) return;
    const id = setTimeout(() => setRecovered(false), CONNECTED_FOR_MS);
    return () => clearTimeout(id);
  }, [recovered]);

  const visible =
    phase === "open" ? recovered : phase === "connecting" ? patient : phase !== "taken" && phase !== "stopped";
  const text = visible ? textOf(phase, attempt) : "";
  const retryable = phase === "not_ready" || (phase === "reconnecting" && attempt > 1);

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="connection-status"
      data-phase={phase}
      className={cn(
        "text-muted-foreground flex min-h-10 items-center justify-between gap-3 border-b px-4 py-1.5 text-sm",
        !visible && "sr-only",
      )}
    >
      <span className="flex min-w-0 items-center gap-2">
        {phase === "open" ? (
          <span aria-hidden className="bg-live inline-block size-1.5 rounded-full" />
        ) : visible ? (
          <DotMatrix state="loading" label={text} className="text-foreground" />
        ) : null}
        <span className="min-w-0 truncate">{text}</span>
      </span>
      {visible && retryable ? (
        <Button variant="outline" size="sm" onClick={onRetry} data-testid="connection-retry" className="max-md:h-10">
          {copy.retry}
        </Button>
      ) : null}
    </div>
  );
}
