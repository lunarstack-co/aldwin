import { create } from "zustand";

type UnsavedState = {
  /** Ids of the forms on screen that hold edits not yet saved. */
  dirty: ReadonlySet<string>;
  /** A move away that waits on the user's answer in the leave dialog. */
  pending: (() => void) | null;
  /** The user chose to leave; history is the router's to change now. */
  leaving: boolean;
  setDirty: (id: string, dirty: boolean) => void;
  /** Runs `leave` now, or once the user agrees to drop their edits. */
  confirmLeave: (leave: () => void) => void;
  answer: (leave: boolean) => void;
};

export const useUnsavedStore = create<UnsavedState>((set, get) => ({
  dirty: new Set(),
  pending: null,
  leaving: false,
  setDirty: (id, dirty) =>
    set((state) => {
      if (state.dirty.has(id) === dirty) return state;
      const next = new Set(state.dirty);
      if (dirty) next.add(id);
      else next.delete(id);
      return { dirty: next, leaving: dirty ? false : state.leaving };
    }),
  confirmLeave: (leave) => {
    if (get().dirty.size === 0) leave();
    else set({ pending: leave });
  },
  answer: (leave) => {
    const pending = get().pending;
    set(leave ? { pending: null, dirty: new Set(), leaving: true } : { pending: null });
    if (leave) pending?.();
  },
}));

/** Moves away now, or after the user agrees to drop unsaved edits; for code-driven navigation. */
export const confirmLeave = (leave: () => void) => useUnsavedStore.getState().confirmLeave(leave);
