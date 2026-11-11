"use client";

import { useCallback, useSyncExternalStore } from "react";

import { DEFAULT_DENSITY, readDensity, writeDensity, type Density } from "@/utils/density";

let current: Density | null = null;
const listeners = new Set<() => void>();

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function snapshot(): Density {
  if (current === null) current = readDensity(storage());
  return current;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The transcript density, remembered in this browser; the server never sees it. */
export function useDensity(): [Density, (density: Density) => void] {
  const density = useSyncExternalStore(subscribe, snapshot, () => DEFAULT_DENSITY);
  const setDensity = useCallback((next: Density) => {
    current = next;
    writeDensity(storage(), next);
    listeners.forEach((listener) => listener());
  }, []);
  return [density, setDensity];
}
