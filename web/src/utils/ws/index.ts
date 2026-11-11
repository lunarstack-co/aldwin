import { CLOSE_CODES, RECONNECT, WS_PATH } from "@/constants/chat";
import { PROTOCOL_VERSION, type Event, type OutboundRequest } from "@/types/protocol";

/** The socket address for this page, carrying the one-time ticket. */
export function socketUrl(pageUrl: string, ticket: string, takeOver: boolean): string {
  const url = new URL(WS_PATH, pageUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.search = "";
  url.searchParams.set("ticket", ticket);
  if (takeOver) url.searchParams.set("take_over", "1");
  return url.toString();
}

export type CloseOutcome = "taken" | "stopped" | "retry";

/**
 * What to do after the socket closes. Another tab holding it means an offer
 * to take over; the server saying goodbye, or too many failed tries, means
 * Aldwin has stopped; anything else is worth another try.
 */
export function outcomeOfClose(code: number, attempt: number, limit: number): CloseOutcome {
  if (code === CLOSE_CODES.alreadyAttached || code === CLOSE_CODES.replaced) return "taken";
  if (code === CLOSE_CODES.goingAway) return "stopped";
  return attempt >= limit ? "stopped" : "retry";
}

/** Delay before reconnect try `attempt` (1-based), doubling up to the cap. */
export function retryDelay(attempt: number): number {
  const scaled = RECONNECT.baseDelayMs * 2 ** Math.max(0, attempt - 1);
  return Math.min(scaled, RECONNECT.maxDelayMs);
}

let counter = 0;

export function requestId(prefix: string): string {
  counter += 1;
  return `${prefix}_${Date.now().toString(36)}_${counter}`;
}

export function frame(request: OutboundRequest): string {
  return JSON.stringify({ v: PROTOCOL_VERSION, channel: "web", sender: "web", ...request });
}

/** One event from a text frame, or `null` for anything that is not one. */
export function parseEvent(text: string): Event | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null) return null;
  const event = raw as Partial<Event>;
  if (event.type !== "event" || typeof event.re !== "string" || typeof event.kind !== "string") {
    return null;
  }
  return event as Event;
}
