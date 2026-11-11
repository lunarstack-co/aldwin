import { RECONNECT } from "@/constants/chat";
import type { ConnectionPhase } from "@/state/connection-store";
import type { Event, OutboundRequest } from "@/types/protocol";
import { ApiError, mintTicket } from "@/utils/api";

import { frame, outcomeOfClose, parseEvent, retryDelay, socketUrl } from "./index";

export type SocketLike = {
  send: (data: string) => void;
  close: () => void;
  onopen: (() => void) | null;
  onmessage: ((message: { data: string }) => void) | null;
  onclose: ((closed: { code: number }) => void) | null;
};

export type ConnectionHandlers = {
  onPhase: (phase: ConnectionPhase, attempt: number) => void;
  onEvent: (event: Event) => void;
  /** The socket is open; `catchUp` when a turn may have run on while this tab was away. */
  onOpen: (catchUp: boolean) => void;
};

export type ConnectionDeps = {
  mintTicket: () => Promise<string>;
  openSocket: (url: string) => SocketLike;
  pageUrl: () => string;
  /** Run `fn` after `ms`; returns a way to cancel it. */
  schedule: (fn: () => void, ms: number) => () => void;
};

export type Connection = {
  start: () => void;
  takeOver: () => void;
  retry: () => void;
  send: (request: OutboundRequest) => void;
  dispose: () => void;
};

export const browserDeps: ConnectionDeps = {
  mintTicket: async () => (await mintTicket()).ticket,
  openSocket: (url) => {
    const ws = new window.WebSocket(url);
    const like: SocketLike = {
      send: (data) => ws.send(data),
      close: () => ws.close(),
      onopen: null,
      onmessage: null,
      onclose: null,
    };
    ws.onopen = () => like.onopen?.();
    ws.onmessage = (message) => like.onmessage?.({ data: String(message.data) });
    ws.onclose = (closed) => like.onclose?.({ code: closed.code });
    return like;
  },
  pageUrl: () => window.location.href,
  schedule: (fn, ms) => {
    const id = setTimeout(fn, ms);
    return () => clearTimeout(id);
  },
};

/**
 * The one socket to Aldwin: a fresh ticket for every connect, a bounded set
 * of retries after a drop, an offer to take over when another tab holds the
 * socket, and a final stop when the server is gone.
 */
export function createConnection(
  handlers: ConnectionHandlers,
  deps: ConnectionDeps = browserDeps,
): Connection {
  let socket: SocketLike | null = null;
  let cancelTimer: (() => void) | null = null;
  let attempt = 0;
  let everOpened = false;
  // Each start or dispose begins a generation; stale callbacks see a different one.
  let generation = 0;

  async function connect(takeOver: boolean) {
    const mine = generation;
    handlers.onPhase(everOpened ? "reconnecting" : "connecting", attempt);
    let ticket: string;
    try {
      ticket = await deps.mintTicket();
    } catch (error) {
      if (mine !== generation) return;
      if (error instanceof ApiError && error.status === 503) {
        handlers.onPhase("not_ready", attempt);
        return;
      }
      afterClose(0, takeOver);
      return;
    }
    if (mine !== generation) return;
    const ws = deps.openSocket(socketUrl(deps.pageUrl(), ticket, takeOver));
    socket = ws;
    ws.onopen = () => {
      if (mine !== generation) return;
      const catchUp = everOpened || takeOver;
      everOpened = true;
      attempt = 0;
      handlers.onPhase(catchUp ? "catching_up" : "open", 0);
      handlers.onOpen(catchUp);
    };
    ws.onmessage = (message) => {
      if (mine !== generation) return;
      const event = parseEvent(message.data);
      if (event !== null) handlers.onEvent(event);
    };
    ws.onclose = (closed) => {
      if (mine !== generation || socket !== ws) return;
      socket = null;
      afterClose(closed.code, false);
    };
  }

  function afterClose(code: number, wasTakeOver: boolean) {
    attempt += 1;
    const outcome = outcomeOfClose(code, attempt, RECONNECT.attempts);
    if (outcome === "taken") {
      attempt = 0;
      handlers.onPhase("taken", 0);
    } else if (outcome === "stopped") {
      handlers.onPhase("stopped", attempt);
    } else {
      handlers.onPhase("reconnecting", attempt);
      cancelTimer = deps.schedule(() => void connect(wasTakeOver), retryDelay(attempt));
    }
  }

  function reset() {
    generation += 1;
    cancelTimer?.();
    cancelTimer = null;
    const ws = socket;
    socket = null;
    ws?.close();
  }

  return {
    start: () => {
      reset();
      void connect(false);
    },
    takeOver: () => {
      reset();
      void connect(true);
    },
    retry: () => {
      reset();
      attempt = 0;
      void connect(false);
    },
    send: (request) => socket?.send(frame(request)),
    dispose: reset,
  };
}
