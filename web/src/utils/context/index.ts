export type ContextTone = "normal" | "amber";

/** Amber only above 85% of the window; exactly 85% is still normal. */
export function contextTone(used: number, limit: number): ContextTone {
  if (limit <= 0) return "normal";
  return used * 100 > limit * 85 ? "amber" : "normal";
}

export function contextPercent(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((used / limit) * 100)));
}
