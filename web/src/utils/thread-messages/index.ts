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

function toolPart(turn: Turn, row: Turn["tools"][number]): Part {
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
}

/**
 * One user and one assistant message per turn, in the order things happened: text, then the tool
 * calls made after it, then the text after those. New pieces are only ever added at the end, so
 * text already on screen never changes place, which would restart its reveal from the first letter.
 */
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
    const answer = turn.discarded ? "" : turn.answer;
    const parts: Part[] = [];
    let from = 0;
    let piece = 0;
    // An empty running text part would draw a lone streaming dot before any words arrive.
    const addText = (text: string, live: boolean) => {
      const shown = text.replace(/^\s+/, "");
      if (shown !== "") {
        parts.push({
          id: `text-${piece}`,
          type: "text",
          text: shown,
          status: live && running ? { type: "running" } : { type: "complete" },
        });
      }
      piece += 1;
    };
    for (const row of turn.tools) {
      const to = Math.max(from, Math.min(row.textBefore, answer.length));
      addText(answer.slice(from, to), false);
      from = to;
      parts.push(toolPart(turn, row));
    }
    addText(answer.slice(from), true);
    messages.push({
      id: assistantMessageId(turn.id),
      role: "assistant",
      content: parts,
      status: statusOf(turn),
    });
  }
  return messages;
}
