"use client";

import { useEffect, useId } from "react";

import { useUnsavedStore } from "@/state/unsaved-store";

/** Marks this form as holding unsaved edits while `dirty` is true, so leaving the page asks first. */
export function useUnsavedChanges(dirty: boolean): void {
  const id = useId();
  const setDirty = useUnsavedStore((s) => s.setDirty);
  useEffect(() => {
    setDirty(id, dirty);
    return () => setDirty(id, false);
  }, [id, dirty, setDirty]);
}
