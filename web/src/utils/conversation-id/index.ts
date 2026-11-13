/** Checked before an id from the address bar reaches the host. */
export const CONVERSATION_ID = /^[A-Za-z0-9_-]{1,128}$/;

export function isConversationId(value: string | null | undefined): value is string {
  return typeof value === "string" && CONVERSATION_ID.test(value);
}
