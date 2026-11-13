import type { Metadata } from "next";

import copy from "@/data/app.json";

export const metadata: Metadata = copy.pages.conversation;

/** The id comes from `?id=`; the shell in the layout opens it. */
export default function ConversationPage() {
  return null;
}
