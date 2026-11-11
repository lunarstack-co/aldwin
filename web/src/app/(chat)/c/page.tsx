import type { Metadata } from "next";

import copy from "@/data/app.json";

export const metadata: Metadata = copy.pages.conversation;

/** One saved conversation, named by `?id=`; the shell in the layout opens and draws it. */
export default function ConversationPage() {
  return null;
}
