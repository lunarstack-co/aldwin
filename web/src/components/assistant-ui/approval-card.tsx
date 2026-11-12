"use client";

import { useEffect, useId, useRef, useState } from "react";

import { DisplayRenderer } from "@/components/assistant-ui/displays";
import { ApprovalCard as ApprovalCardElement } from "@/components/assistant-ui/elements/approval-card";
import { PermissionGrant } from "@/components/assistant-ui/elements/permission-grant";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import copy from "@/data/chat/approval.json";
import { useSession } from "@/providers/chat-session-provider";
import type { TurnApproval } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import type { Display, GrantDisplay } from "@/types/protocol";
import { fill } from "@/utils/text";
import { MAX_APPROVAL_REASON_CHARS } from "@/utils/ws/session";

const titlesByTool = copy.titles as Record<string, string>;

function actionTitle(tool?: string): string {
  if (tool && tool in titlesByTool) return titlesByTool[tool];
  return copy.titles.default;
}

function grantCapability(summary: string, grant: GrantDisplay): string {
  const reach = grant.reach[0] ?? "";
  if (
    summary &&
    summary !== titlesByTool.write_file &&
    !summary.startsWith("Read outside workspace:") &&
    !summary.startsWith("Approve ")
  ) {
    return summary;
  }
  const templates = copy.grantCapabilities as Record<string, string>;
  const template = templates[grant.scope] ?? copy.grantCapabilities.folder;
  return fill(template, { reach });
}

export function ApprovalCardImpl({
  approval,
  resultDisplay,
}: {
  approval: TurnApproval;
  resultDisplay?: Display | null;
}) {
  const session = useSession();
  const cardRef = useRef<HTMLDivElement>(null);
  const reasonInputRef = useRef<HTMLInputElement>(null);
  const reasonId = useId();
  const [denyState, setDenyState] = useState<{ id: string; reason: string } | null>(null);
  const { event, state, answer } = approval;
  const denying = state === "request" && denyState?.id === event.approval_id;
  const reason = denying ? (denyState?.reason ?? "") : "";

  useEffect(() => {
    if (state !== "request") return;
    if (useConnectionStore.getState().phase === "taken") return;
    if (document.querySelector('[role="alertdialog"], [role="dialog"]')) return;
    const frame = requestAnimationFrame(() => {
      const firstBtn = cardRef.current?.querySelector<HTMLButtonElement>(
        'button:not([disabled])',
      );
      firstBtn?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [event.approval_id, state]);

  useEffect(() => {
    if (denying) {
      reasonInputRef.current?.focus();
    }
  }, [denying]);

  const submitDeny = () => {
    const trimmed = reason.slice(0, MAX_APPROVAL_REASON_CHARS);
    setDenyState(null);
    session.approve("no", trimmed || undefined);
  };

  const denyForm =
    denying ? (
      <form
        data-testid="approval-deny-form"
        onSubmit={(e) => {
          e.preventDefault();
          submitDeny();
        }}
        className="flex flex-col gap-2"
      >
        <label htmlFor={reasonId} className="text-muted-foreground text-xs">
          {copy.reasonLabel}
        </label>
        <Input
          ref={reasonInputRef}
          id={reasonId}
          data-testid="approval-deny-reason"
          value={reason}
          maxLength={MAX_APPROVAL_REASON_CHARS}
          onChange={(e) =>
            setDenyState({
              id: event.approval_id,
              reason: e.target.value.slice(0, MAX_APPROVAL_REASON_CHARS),
            })
          }
          placeholder={copy.reasonPlaceholder}
        />
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setDenyState(null)}
          >
            {copy.cancelDeny}
          </Button>
          <Button
            type="submit"
            variant="default"
            size="sm"
            data-testid="approval-deny-confirm"
          >
            {copy.sendDeny}
          </Button>
        </div>
      </form>
    ) : null;

  if (event.display?.kind === "grant") {
    const grantDisplay = event.display;
    const scopeValue =
      state === "request"
        ? "pending"
        : state === "denied"
          ? "denied"
          : answer === "yes_session"
            ? "session"
            : "once";
    const statusLabel =
      state === "running"
        ? copy.running
        : state === "denied"
          ? copy.denied
          : answer === "yes_session"
            ? copy.grantedSession
            : copy.grantedOnce;

    return (
      <div ref={cardRef} data-testid="approval-card" data-approval-id={event.approval_id}>
        <PermissionGrant
          capability={grantCapability(event.summary, grantDisplay)}
          requester={copy.requester}
          requestedByLabel={fill(copy.requestedBy, { requester: copy.requester })}
          grantsLabel={copy.grants}
          reach={grantDisplay.reach}
          scope={scopeValue}
          allowOnceLabel={copy.allowOnce}
          sessionLabel={copy.forThisChat}
          denyLabel={copy.deny}
          statusLabel={statusLabel}
          footerSlot={denyForm}
          onGrant={
            state === "request" && !denying
              ? (choice) => {
                  if (choice === "once") session.approve("yes");
                  else if (choice === "session") session.approve("yes_session");
                  else setDenyState({ id: event.approval_id, reason: "" });
                }
              : undefined
          }
        />
      </div>
    );
  }

  const subtitle = event.connector_title
    ? fill(copy.inApp, { summary: event.summary, app: event.connector_title })
    : event.summary;

  const riskyBadges =
    event.risky && event.risky.length > 0 ? (
      <div className="mt-1 flex flex-wrap gap-1.5" data-testid="approval-risky">
        {event.risky.map((label) => (
          <Badge
            key={label}
            variant="outline"
            className="border-amber-600/30 bg-amber-500/15 text-amber-800 dark:border-amber-400/30 dark:bg-amber-400/15 dark:text-amber-300"
          >
            <span className="sr-only">{fill(copy.riskLabel, { label })}: </span>
            {label}
          </Badge>
        ))}
      </div>
    ) : null;

  const activeDisplay = resultDisplay ?? event.display ?? null;
  const body = activeDisplay ? (
    <DisplayRenderer
      display={activeDisplay}
      pending={resultDisplay == null && (state === "request" || state === "running")}
    />
  ) : event.detail ? (
    <pre
      data-testid="approval-detail"
      className="bg-code-surface overflow-x-auto rounded-xl px-3.5 py-2.5 font-mono text-xs break-words whitespace-pre-wrap"
    >
      <code>{event.detail}</code>
    </pre>
  ) : null;

  const statusLabel =
    state === "running"
      ? copy.running
      : state === "done"
        ? copy.done
        : copy.denied;

  return (
    <div ref={cardRef} data-testid="approval-card" data-approval-id={event.approval_id}>
      <ApprovalCardElement
        state={state}
        title={actionTitle(event.tool)}
        subtitle={subtitle}
        badges={riskyBadges}
        footerSlot={denyForm}
        allowOnceLabel={copy.allow}
        alwaysAllowLabel={copy.alwaysAllow}
        denyLabel={copy.deny}
        statusLabel={statusLabel}
        onAllowOnce={
          state === "request" && !denying ? () => session.approve("yes") : undefined
        }
        onAlwaysAllow={
          state === "request" && !denying && event.grantable
            ? () => session.approve("yes_session")
            : undefined
        }
        onDeny={
          state === "request" && !denying
            ? () => setDenyState({ id: event.approval_id, reason: "" })
            : undefined
        }
      >
        {body}
      </ApprovalCardElement>
    </div>
  );
}
