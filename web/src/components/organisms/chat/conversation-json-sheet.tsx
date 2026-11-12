"use client";

import { CheckIcon, CopyIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import copy from "@/data/chat/conversation-json.json";
import { useCopy } from "@/hooks/use-copy";
import { ApiError, getConversationJson } from "@/utils/api";

type FetchState =
  | { status: "loading" }
  | { status: "ready"; formatted: string }
  | { status: "not_found" }
  | { status: "error" };

export function ConversationJsonSheet({
  open,
  onOpenChange,
  sessionId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sessionId: string | null;
}) {
  const { outcome, copy: copyText } = useCopy();
  const [state, setState] = useState<{ key: string; fetch: FetchState } | null>(null);
  const currentKey = open && sessionId ? sessionId : "";
  const activeState: FetchState =
    state?.key === currentKey ? state.fetch : { status: "loading" };

  useEffect(() => {
    if (!open || !sessionId) return;
    let active = true;
    void getConversationJson(sessionId).then(
      (body) => {
        if (!active) return;
        setState({
          key: sessionId,
          fetch: { status: "ready", formatted: JSON.stringify(body, null, 2) },
        });
      },
      (err: unknown) => {
        if (!active) return;
        const notFound = err instanceof ApiError && err.status === 404;
        setState({
          key: sessionId,
          fetch: { status: notFound ? "not_found" : "error" },
        });
      },
    );
    return () => {
      active = false;
    };
  }, [open, sessionId]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        data-testid="conversation-json-sheet"
        className="w-full sm:max-w-lg"
      >
        <SheetHeader className="pe-10">
          <SheetTitle>{copy.title}</SheetTitle>
          <SheetDescription>{copy.description}</SheetDescription>
        </SheetHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pb-4">
          {activeState.status === "loading" ? (
            <p
              role="status"
              data-testid="conversation-json-loading"
              className="text-muted-foreground text-sm"
            >
              {copy.loading}
            </p>
          ) : activeState.status === "not_found" ? (
            <p
              role="alert"
              data-testid="conversation-json-error"
              className="text-muted-foreground text-sm"
            >
              {copy.notFound}
            </p>
          ) : activeState.status === "error" ? (
            <p
              role="alert"
              data-testid="conversation-json-error"
              className="text-destructive text-sm"
            >
              {copy.error}
            </p>
          ) : (
            <>
              <div className="flex justify-end">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  data-testid="conversation-json-copy"
                  onClick={() => copyText(activeState.formatted)}
                  className="max-md:min-h-10"
                >
                  {outcome === "copied" ? <CheckIcon aria-hidden /> : <CopyIcon aria-hidden />}
                  <span role="status" aria-live="polite">
                    {outcome === "copied" ? copy.copied : copy.copy}
                  </span>
                </Button>
              </div>
              <pre
                data-testid="conversation-json-body"
                className="bg-code-surface min-h-0 flex-1 overflow-auto rounded-xl p-3.5 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap"
              >
                <code>{activeState.formatted}</code>
              </pre>
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
