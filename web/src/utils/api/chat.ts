import { WS_TICKET_PATH } from "@/constants/chat";

import { call } from "./client";

export type Ticket = { ticket: string; expires_in: number };

/** A one-time ticket for the WebSocket; 503 means no host is serving yet. */
export function mintTicket(): Promise<Ticket> {
  return call("POST", WS_TICKET_PATH);
}
