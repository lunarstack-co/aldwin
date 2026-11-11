/** The message to show for anything a call or action threw. */
export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
