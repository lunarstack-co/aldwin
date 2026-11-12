import { call } from "./client";

export function getConversationJson(id: string): Promise<unknown> {
  return call<unknown>("GET", `/v1/conversations/${encodeURIComponent(id)}`);
}
