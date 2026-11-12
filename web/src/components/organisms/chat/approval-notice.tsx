"use client";

import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import copy from "@/data/chat/approval-notice.json";
import { useSession } from "@/providers/chat-session-provider";
import { useChatStore } from "@/state/chat-store";
import { fill } from "@/utils/text";

/** Says plainly that the running answer waits on an approval the web cannot give yet, and offers Stop. */
export function ApprovalNotice() {
  const session = useSession();
  const summary = useChatStore((s) => s.waitingApproval);
  const stopping = useChatStore((s) => s.turns.find((t) => t.id === s.activeId)?.status === "stopping");
  if (summary === null) return null;
  return (
    <Callout type="warning" title={copy.title} data-testid="approval-notice">
      <p>{fill(copy.body, { summary: summary.replace(/[.\s]+$/, "") })}</p>
      <Button variant="outline" size="sm" disabled={stopping} onClick={() => session.stop()} className="mt-2 max-md:h-10">
        {copy.stop}
      </Button>
    </Callout>
  );
}
