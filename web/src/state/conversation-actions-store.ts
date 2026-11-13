import { create } from "zustand";

export type ConversationTarget = { id: string; title: string };

type ConversationActionsState = {
  renaming: ConversationTarget | null;
  deleting: ConversationTarget | null;
  askRename: (target: ConversationTarget) => void;
  askDelete: (target: ConversationTarget) => void;
  close: () => void;
};

/** The rename and delete dialogs live once in the shell; menus anywhere open them through here. */
export const useConversationActions = create<ConversationActionsState>((set) => ({
  renaming: null,
  deleting: null,
  askRename: (target) => set({ renaming: target, deleting: null }),
  askDelete: (target) => set({ deleting: target, renaming: null }),
  close: () => set({ renaming: null, deleting: null }),
}));
