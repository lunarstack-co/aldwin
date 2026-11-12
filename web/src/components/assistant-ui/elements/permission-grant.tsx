"use client";

import type { ComponentProps, ReactNode } from "react";
import { KeyRoundIcon } from "lucide-react";
import { cn } from "@/utils/cn";
import approvalCopy from "@/data/chat/approval.json";
import { field, ghostButton, inkButton, mono, paper } from "./surfaces";
import { useReceiptFocus } from "./receipt-focus";

export type GrantScope = "once" | "session" | "always" | "denied";

export function PermissionGrant({
  capability,
  requester,
  reach,
  scope,
  onGrant,
  requestedByLabel,
  grantsLabel = "this grants",
  allowOnceLabel = approvalCopy.allowOnce,
  sessionLabel = approvalCopy.forThisChat,
  denyLabel = "Deny",
  statusLabel,
  footerSlot,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  "children" | "capability" | "requester" | "reach" | "scope" | "onGrant"
> & {
  capability: string;
  requester: string;
  reach: readonly string[];
  scope: GrantScope | "pending";
  onGrant?: (scope: GrantScope) => void;
  requestedByLabel?: string | undefined;
  grantsLabel?: string | undefined;
  allowOnceLabel?: string | undefined;
  sessionLabel?: string | undefined;
  denyLabel?: string | undefined;
  statusLabel?: string | undefined;
  footerSlot?: ReactNode;
}) {
  const { receiptRef, focusHandlers } = useReceiptFocus(props);
  return (
    <div
      data-slot="permission-grant"
      data-grant-scope={scope}
      className={cn(
        paper,
        "flex w-full max-w-full flex-col gap-3.5 rounded-[20px] p-4",
        className,
      )}
      {...props}
      {...focusHandlers}
    >
      <div className="flex items-center gap-2.5">
        <span
          aria-hidden
          className="bg-foreground/[0.05] text-muted-foreground flex size-7 shrink-0 items-center justify-center rounded-lg"
        >
          <KeyRoundIcon className="size-3.5" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="break-words text-[13.5px] font-medium">
            {capability}
          </span>
          <span className="text-muted-foreground truncate text-xs">
            {requestedByLabel ?? `requested by ${requester}`}
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <span className={cn(mono, "text-muted-foreground")}>{grantsLabel}</span>
        {reach.map((item) => (
          <span
            key={item}
            className="text-foreground/70 flex items-baseline gap-2 break-all text-xs"
          >
            <span
              aria-hidden
              className="bg-foreground/20 size-1 shrink-0 rounded-full"
            />
            {item}
          </span>
        ))}
      </div>

      {footerSlot}

      {!footerSlot && (
        <div className="flex min-h-8 flex-wrap items-center justify-end gap-2">
          {scope === "pending" ? (
            onGrant ? (
              <>
                <button
                  type="button"
                  data-slot="permission-grant-once"
                  onClick={() => onGrant("once")}
                  className={cn(
                    inkButton,
                    "flex h-8 cursor-pointer items-center rounded-full px-3.5 text-xs font-medium whitespace-nowrap max-md:min-h-10",
                  )}
                >
                  {allowOnceLabel}
                </button>
                <button
                  type="button"
                  data-slot="permission-grant-session"
                  onClick={() => onGrant("session")}
                  className={cn(
                    ghostButton,
                    "h-8 cursor-pointer px-3.5 text-xs font-medium whitespace-nowrap max-md:min-h-10",
                  )}
                >
                  {sessionLabel}
                </button>
                <button
                  type="button"
                  data-slot="permission-grant-deny"
                  onClick={() => onGrant("denied")}
                  className={cn(
                    ghostButton,
                    "h-8 cursor-pointer px-3.5 text-xs font-medium whitespace-nowrap max-md:min-h-10",
                  )}
                >
                  {denyLabel}
                </button>
              </>
            ) : (
              <span
                key={scope}
                className={cn(
                  field,
                  mono,
                  "text-foreground/80 rounded-full px-2.5 py-1.5",
                )}
              >
                {statusLabel ?? "pending"}
              </span>
            )
          ) : (
            <span
              key={scope}
              ref={receiptRef}
              tabIndex={-1}
              role="status"
              className={cn(
                field,
                mono,
                "text-foreground/80 rounded-full px-2.5 py-1.5",
              )}
            >
              {statusLabel ?? (scope === "denied" ? "denied" : `granted · ${scope}`)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
