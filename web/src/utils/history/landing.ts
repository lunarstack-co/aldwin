/** An empty conversation is already a new chat, so the host is not asked again. */
export function shouldCallSessionsNew(turnCount: number): boolean {
  return turnCount > 0;
}
