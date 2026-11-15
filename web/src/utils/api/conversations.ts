import { call } from "./client";

export function getConversationJson(id: string): Promise<unknown> {
  return call<unknown>("GET", `/v1/conversations/${encodeURIComponent(id)}`);
}

/** A saved chat whose messages match a search, with the words around the match. */
export type ConversationMatch = { id: string; snippet: string };

export function searchConversations(query: string): Promise<{ items: ConversationMatch[] }> {
  return call("GET", `/v1/conversations/search?q=${encodeURIComponent(query)}`);
}
