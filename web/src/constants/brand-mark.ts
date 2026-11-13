export const MARK_BLOB =
  "M128,36 C206,34 230,58 231,130 C232,204 210,226 128,226 C46,226 24,206 25,132 C26,60 50,38 128,36 Z";

/** Silhouette bounds on the kit's 256-unit grid, for a tight viewBox. */
export const MARK_BOUNDS = { x: 25, y: 36, width: 206, height: 190 } as const;

export const MARK_EYES = [
  { x: 84, y: 88 },
  { x: 148, y: 88 },
] as const;

export const EYE_WIDTH = 24;
export const EYE_HEIGHT = 48;

/** Closed eyes, for a blink and the wink's right eye: wider and flat. */
export const MARK_EYES_CLOSED = [
  { x: 80, y: 106, width: 32, height: 14 },
  { x: 144, y: 106, width: 32, height: 14 },
] as const;

/** Happy eyes: two "^" strokes. */
export const MARK_EYES_HAPPY = ["80,124 96,104 112,124", "144,124 160,104 176,124"] as const;
