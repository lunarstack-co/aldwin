"use client";

import { ActionBarPrimitive, MessagePrimitive, useAuiState } from "@assistant-ui/react";
import { Check, Copy, RotateCcw } from "lucide-react";

import { Button } from "@/components/atoms/button";
import { FormStatus } from "@/components/atoms/form-message";
import { ErrorRow } from "@/components/molecules/error-row";
import { MarkdownText } from "@/components/molecules/markdown-text";
import { ToolCard } from "@/components/molecules/tool-card";
import errorCopy from "@/data/chat/error-row.json";
import actions from "@/data/chat/message-actions.json";
import copy from "@/data/chat/transcript.json";

import { ToolGroup } from "./tool-group";

const components = { Text: MarkdownText, tools: { Fallback: ToolCard }, ToolGroup };

function Outcome() {
  const status = useAuiState((s) => s.message.status);
  if (status === undefined || status.type !== "incomplete") return null;
  if (status.reason === "cancelled") return <FormStatus>{copy.stopped}</FormStatus>;
  if (status.reason === "length") return <FormStatus>{copy.maxSteps}</FormStatus>;
  if (status.reason === "error") {
    return <ErrorRow message={String(status.error ?? "")} labels={errorCopy} />;
  }
  return null;
}

function Actions() {
  const copied = useAuiState((s) => s.message.isCopied);
  return (
    <ActionBarPrimitive.Root
      hideWhenRunning
      autohide="never"
      className="flex items-center gap-1"
      data-testid="message-actions"
    >
      <ActionBarPrimitive.Copy asChild>
        <Button aria-label={actions.copy} data-testid="copy-answer">
          {copied ? <Check aria-hidden className="size-4" /> : <Copy aria-hidden className="size-4" />}
          <span role="status" aria-live="polite">
            {copied ? actions.copied : actions.copy}
          </span>
        </Button>
      </ActionBarPrimitive.Copy>
      <ActionBarPrimitive.Reload asChild>
        <Button data-testid="retry-turn">
          <RotateCcw aria-hidden className="size-4" />
          {actions.retry}
        </Button>
      </ActionBarPrimitive.Reload>
    </ActionBarPrimitive.Root>
  );
}

/** The answer to one question: the work behind it, the text, and what to do next. */
export function AssistantMessage() {
  const status = useAuiState((s) => s.message.status?.type ?? "complete");
  const hasText = useAuiState((s) => s.message.content.some((p) => p.type === "text" && p.text !== ""));
  return (
    <MessagePrimitive.Root
      className="flex flex-col gap-3"
      aria-label={copy.answer}
      data-testid="turn"
      data-status={status}
    >
      <MessagePrimitive.Parts components={components} />
      <Outcome />
      {status !== "running" && hasText ? <Actions /> : null}
    </MessagePrimitive.Root>
  );
}
