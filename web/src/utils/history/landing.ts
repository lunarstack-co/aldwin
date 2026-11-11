/**
 * Whether New chat needs the host at all. An empty conversation is already a
 * new chat, so asking again would only make the host look for another one.
 */
export function shouldCallSessionsNew(turnCount: number): boolean {
  return turnCount > 0;
}
