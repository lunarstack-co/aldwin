import { create } from "zustand";

/** `taken`: another tab holds the socket. `not_ready`: no host yet. `stopped`: Aldwin is gone. */
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
