import { create } from "zustand";

/**
 * Where the socket is. `taken` means another tab holds it, `not_ready` that
 * no host is serving yet, and `stopped` that Aldwin is gone for good.
 */
export type ConnectionPhase =
  | "connecting"
  | "open"
  | "catching_up"
  | "reconnecting"
  | "taken"
  | "not_ready"
  | "stopped";

type ConnectionState = {
  phase: ConnectionPhase;
  attempt: number;
  setPhase: (phase: ConnectionPhase, attempt?: number) => void;
};

export const useConnectionStore = create<ConnectionState>((set) => ({
  phase: "connecting",
  attempt: 0,
  setPhase: (phase, attempt = 0) => set({ phase, attempt }),
}));
