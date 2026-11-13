import type { ThreadMessageLike } from "@assistant-ui/react";

import type { Turn, TurnApproval } from "@/state/chat-store";
import type { Display } from "@/types/protocol";

type Part = Exclude<ThreadMessageLike["content"], string>[number];
type Status = NonNullable<ThreadMessageLike["status"]>;

const USER = "_user";
const ASSISTANT = "_assistant";

export type ToolResult = {
  summary: string;
  durationMs: number;
  preview: string;
  display?: Display;
};

export type ToolSource = {
  connectorTitle: string | null;
  remoteTool: string | null;
  reason: string | null;
  approval?: TurnApproval;
};

export function userMessageId(turnId: string): string {
  return `${turnId}${USER}`;
}

export function assistantMessageId(turnId: string): string {
  return `${turnId}${ASSISTANT}`;
}

/** The turn behind a message id, whichever side of the exchange it is. */
export function turnIdOf(messageId: string): string {
  return messageId.replace(/_(user|assistant)$/, "");
}

export function isRunning(turn: Turn): boolean {
  return turn.status === "streaming" || turn.status === "stopping";
}

function statusOf(turn: Turn): Status {
  switch (turn.status) {
    case "streaming":
    case "stopping":
      return { type: "running" };
    case "answered":
      return { type: "complete", reason: "stop" };
    case "stopped":
      return { type: "incomplete", reason: "cancelled" };
    case "max_steps":
      return { type: "incomplete", reason: "length" };
    case "error":
      return { type: "incomplete", reason: "error", error: turn.error ?? "" };
  }
}

/** Returns a turn's approval when no tool row owns its `call_id` yet (or it has none). */
export function unattachedTurnApproval(turn: Turn): TurnApproval | null {
  if (!turn.approval) return null;
  const callId = turn.approval.event.call_id;
  if (!callId) return turn.approval;
  const matched = turn.tools.some((row) => row.callId === callId);
  return matched ? null : turn.approval;
}

/** One user and one assistant message per turn: tool calls first, then the answer. */
export function turnsToMessages(turns: readonly Turn[]): ThreadMessageLike[] {
  const messages: ThreadMessageLike[] = [];
  for (const turn of turns) {
    if (turn.userText) {
      messages.push({
        id: userMessageId(turn.id),
        role: "user",
        content: [{ type: "text", text: turn.userText }],
      });
    }
    const running = isRunning(turn);
    const parts: Part[] = turn.tools.map((row) => {
      const rowApproval = turn.approvalsByCallId[row.callId];
      return {
        type: "tool-call",
        toolCallId: row.callId,
        toolName: row.name,
        args: {},
        argsText: row.argsPreview,
        result:
          row.ok === null
            ? undefined
            : ({
                summary: row.summary,
                durationMs: row.durationMs,
                preview: row.preview,
                ...(row.display ? { display: row.display } : {}),
              } satisfies ToolResult),
        artifact: {
          connectorTitle: row.connectorTitle,
          remoteTool: row.remoteTool,
          reason: row.reason,
          ...(rowApproval ? { approval: rowApproval } : {}),
        } satisfies ToolSource,
        isError: row.ok === false,
      };
    });
    // An empty running text part would draw a lone streaming dot before any words arrive.
    if (turn.answer && !turn.discarded) {
      parts.push({
        type: "text",
        text: turn.answer,
        status: running ? { type: "running" } : { type: "complete" },
      });
    }
    messages.push({
      id: assistantMessageId(turn.id),
      role: "assistant",
      content: parts,
      status: statusOf(turn),
    });
  }
  return messages;
}
