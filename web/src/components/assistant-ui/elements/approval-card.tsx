"use client";

import { useId, type ComponentProps, type ReactNode } from "react";
import { CheckIcon, Loader2Icon, TerminalIcon, XIcon } from "lucide-react";
import { cn } from "@/utils/cn";
import { field, ghostButton, inkButton, mono, paper } from "./surfaces";
import { useReceiptFocus } from "./receipt-focus";

export type ApprovalState = "request" | "running" | "done" | "denied";

const receiptText: Record<Exclude<ApprovalState, "request">, string> = {
  running: "Running",
  done: "Finished",
  denied: "Denied",
};

export function ApprovalCard({
  state,
  command,
  title,
  subtitle,
  description,
  details,
  badges,
  children,
  footerSlot,
  variant = "default",
  icon,
  onAllowOnce,
  onAlwaysAllow,
  onDeny,
  allowOnceLabel = "Allow",
  alwaysAllowLabel = "Always allow",
  denyLabel = "Deny",
  statusLabel,
  className,
  ...props
}: Omit<
  ComponentProps<"div">,
  | "children"
  | "state"
  | "command"
  | "title"
  | "subtitle"
  | "description"
  | "details"
  | "variant"
  | "icon"
  | "onAllowOnce"
  | "onAlwaysAllow"
  | "onDeny"
  | "allowOnceLabel"
  | "alwaysAllowLabel"
  | "denyLabel"
  | "statusLabel"
> & {
  state: ApprovalState;
  command?: string | undefined;
  title: string;
  subtitle: string;
  description?: string | undefined;
  details?: readonly { label: string; value: string }[] | undefined;
  badges?: ReactNode;
  children?: ReactNode;
  footerSlot?: ReactNode;
  variant?: "default" | "destructive" | undefined;
  icon?: ReactNode | undefined;
  onAllowOnce?: (() => void) | undefined;
  onAlwaysAllow?: (() => void) | undefined;
  onDeny?: (() => void) | undefined;
  allowOnceLabel?: string | undefined;
  alwaysAllowLabel?: string | undefined;
  denyLabel?: string | undefined;
  statusLabel?: string | undefined;
}) {
  const titleId = useId();
  const descriptionId = useId();
  const { receiptRef, focusHandlers } = useReceiptFocus<HTMLDivElement>(props);

  return (
    <div
      {...props}
      {...focusHandlers}
      role="group"
      data-variant={variant}
      data-slot="approval-card"
      data-approval-state={state}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      className={cn(
        paper,
        "flex w-full max-w-full flex-col gap-3.5 rounded-[20px] p-4",
        className,
      )}
    >
      <div className="flex items-start gap-3">
        <span
          aria-hidden
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-xl",
            variant === "destructive"
              ? "bg-red-600/10 text-red-600 dark:bg-red-400/10 dark:text-red-400"
              : "bg-foreground/[0.05] text-muted-foreground",
          )}
        >
          {icon ?? <TerminalIcon className="size-4" />}
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <p id={titleId} className="text-[13.5px] font-medium">
            {title}
          </p>
          <p className="text-muted-foreground break-words text-xs">{subtitle}</p>
          {badges}
        </div>
      </div>

      {description ? (
        <p id={descriptionId} className="text-foreground/60 text-[13px]">
          {description}
        </p>
      ) : null}

      {children}

      {command ? (
        <div
          className={cn(
            field,
            "text-foreground/70 overflow-x-auto rounded-xl px-3.5 py-2.5 font-mono text-xs break-words whitespace-pre-wrap",
          )}
        >
          {command}
        </div>
      ) : null}

      {details?.length ? (
        <dl
          className={cn(field, "flex flex-col gap-2 rounded-xl px-3.5 py-2.5")}
        >
          {details.map((detail, index) => (
            <div
              key={`${detail.label}-${index}`}
              className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-4 text-xs"
            >
              <dt className={cn(mono, "text-muted-foreground")}>
                {detail.label}
              </dt>
              <dd className="text-foreground/80 break-words">{detail.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {footerSlot}

      <div className="flex min-h-8 flex-wrap items-center justify-end gap-2">
        {state === "request" ? (
          <>
            {onAllowOnce && (
              <button
                type="button"
                data-slot="approval-card-allow-once"
                onClick={onAllowOnce}
                className={cn(
                  variant === "destructive"
                    ? "text-background bg-red-600 transition-[background-color,scale] duration-150 hover:bg-red-600/90 active:scale-[0.96] motion-reduce:transition-none dark:bg-red-400 dark:text-red-950 dark:hover:bg-red-400/90"
                    : inkButton,
                  "flex h-8 cursor-pointer items-center rounded-full px-3.5 text-xs font-medium whitespace-nowrap max-md:min-h-10",
                )}
              >
                {allowOnceLabel}
              </button>
            )}
            {onAlwaysAllow && (
              <button
                type="button"
                data-slot="approval-card-always-allow"
                onClick={onAlwaysAllow}
                className={cn(
                  ghostButton,
                  "h-8 cursor-pointer px-3.5 text-xs font-medium whitespace-nowrap max-md:min-h-10",
                )}
              >
                {alwaysAllowLabel}
              </button>
            )}
            {onDeny && (
              <button
                type="button"
                data-slot="approval-card-deny"
                onClick={onDeny}
                className={cn(
                  ghostButton,
                  "h-8 cursor-pointer px-3.5 text-xs font-medium whitespace-nowrap max-md:min-h-10",
                )}
              >
                {denyLabel}
              </button>
            )}
          </>
        ) : (
          <div
            key={state}
            ref={receiptRef}
            tabIndex={-1}
            role="status"
            className="text-muted-foreground flex items-center gap-2 text-xs"
          >
            {state === "running" ? (
              <>
                <Loader2Icon aria-hidden className="text-muted-foreground size-3.5 animate-spin motion-reduce:animate-none" />
                {statusLabel ?? receiptText.running}
              </>
            ) : state === "done" ? (
              <>
                <CheckIcon aria-hidden className="size-3.5 text-emerald-500" />
                {statusLabel ?? receiptText.done}
              </>
            ) : (
              <>
                <XIcon aria-hidden className="size-3.5 text-red-600 dark:text-red-400" />
                {statusLabel ?? receiptText.denied}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
