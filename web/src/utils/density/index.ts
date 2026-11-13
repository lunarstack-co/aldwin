import { DENSITIES, DENSITY_KEY } from "@/constants/chat";

export type Density = (typeof DENSITIES)[number];

/** Clean mode: a collapsed work chip instead of the tool stream. */
export const DEFAULT_DENSITY: Density = "clean";

export function isDensity(value: unknown): value is Density {
  return typeof value === "string" && (DENSITIES as readonly string[]).includes(value);
}

type Store = Pick<Storage, "getItem" | "setItem"> | null | undefined;

export function readDensity(storage: Store): Density {
  try {
    const raw = storage?.getItem(DENSITY_KEY);
    return isDensity(raw) ? raw : DEFAULT_DENSITY;
  } catch {
    return DEFAULT_DENSITY;
  }
}

export function writeDensity(storage: Store, density: Density): void {
  try {
    storage?.setItem(DENSITY_KEY, density);
  } catch {
    // Private windows and blocked storage keep the choice for this page only.
  }
}
