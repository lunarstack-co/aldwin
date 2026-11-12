"use client";

import { memo, type ComponentPropsWithoutRef, type FC, type ReactNode } from "react";
import {
  ComposerPrimitive,
  defaultDirectiveFormatter,
  type DirectiveFormatter,
  type TriggerItem,
} from "@assistant-ui/react";
import { SparklesIcon } from "lucide-react";

import { cn } from "@/utils/cn";

type IconComponent = FC<{ className?: string }>;

type DirectiveBehaviorProps = {
  /** Formatter used to serialize the selected item into composer text. */
  formatter?: DirectiveFormatter | undefined;
  /** Called after the directive text has been inserted into the composer. */
  onInserted?: ((item: TriggerItem) => void) | undefined;
};

type ActionBehaviorProps = {
  /** Formatter used to serialize the audit-trail chip (when `removeOnExecute` is false). */
  formatter?: DirectiveFormatter | undefined;
  /** Invoked with the selected item at the moment of selection. */
  onExecute: (item: TriggerItem) => void;
  /** If `true`, strip the trigger text from the composer after executing. @default false */
  removeOnExecute?: boolean | undefined;
};

type ComposerTriggerPopoverBaseProps = Omit<
  ComponentPropsWithoutRef<typeof ComposerPrimitive.TriggerPopover>,
  "children"
> & {
  /**
   * Maps icon keys to components. Items look up via `item.metadata?.icon`
   * (string); categories look up via their `id`.
   */
  iconMap?: Record<string, IconComponent>;
  /** Fallback icon when no entry in `iconMap` matches. */
  fallbackIcon?: IconComponent;
  /** Shown when no items match, or in place of the list when it could not load. */
  emptyItemsLabel: ReactNode;
  /** Label shown while an async adapter is resolving items. */
  loadingLabel: string;
  /** Rendered inside the popover after the list; for helpers that need the popover's context. */
  children?: ReactNode;
};

type ComposerTriggerPopoverProps = ComposerTriggerPopoverBaseProps &
  (
    | {
        /** Insert-directive behavior. */
        directive: DirectiveBehaviorProps;
        action?: never;
      }
    | {
        /** Action behavior. */
        action: ActionBehaviorProps;
        directive?: never;
      }
  );

function resolveIcon(
  iconKey: string | undefined,
  iconMap: Record<string, IconComponent> | undefined,
  fallback: IconComponent,
): IconComponent {
  if (iconKey && iconMap?.[iconKey]) return iconMap[iconKey]!;
  return fallback;
}

type ItemsProps = {
  iconMap: Record<string, IconComponent> | undefined;
  fallbackIcon: IconComponent;
  emptyLabel: ReactNode;
  loadingLabel: string;
  isLoading: boolean;
};

const Items: FC<ItemsProps> = ({
  iconMap,
  fallbackIcon,
  emptyLabel,
  loadingLabel,
  isLoading,
}) => {
  return (
    <ComposerPrimitive.TriggerPopoverItems>
      {(items) => (
        <div
          data-slot="composer-trigger-popover-items"
          className="flex flex-col"
        >
          <div className="py-1">
            {items.map((item, index) => {
              const iconKey =
                typeof item.metadata?.icon === "string"
                  ? item.metadata.icon
                  : undefined;
              const Icon = resolveIcon(iconKey, iconMap, fallbackIcon);
              return (
                <ComposerPrimitive.TriggerPopoverItem
                  key={item.id}
                  item={item}
                  index={index}
                  className="hover:bg-accent focus:bg-accent data-[highlighted]:bg-accent flex w-full flex-col items-start gap-0.5 px-3 py-2 text-start transition-colors outline-none"
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    <Icon className="text-primary size-3.5" />
                    {item.label}
                  </span>
                  {item.description && (
                    <span className="text-foreground/70 ms-5.5 text-xs leading-tight">
                      {item.description}
                    </span>
                  )}
                </ComposerPrimitive.TriggerPopoverItem>
              );
            })}
            {items.length === 0 && (
              <div className="text-muted-foreground px-3 py-2 text-sm">
                {isLoading ? loadingLabel : emptyLabel}
              </div>
            )}
          </div>
        </div>
      )}
    </ComposerPrimitive.TriggerPopoverItems>
  );
};

/**
 * Pre-built popover UI for a trigger-driven picker (mentions, slash commands, etc).
 * Pass exactly one of `directive` (inserts a chip) or `action` (fires a handler).
 */
const ComposerTriggerPopoverImpl: FC<ComposerTriggerPopoverProps> = ({
  iconMap,
  fallbackIcon = SparklesIcon,
  emptyItemsLabel,
  loadingLabel,
  children,
  isLoading = false,
  className,
  directive,
  action,
  ...props
}) => {
  return (
    <ComposerPrimitive.TriggerPopover
      data-slot="composer-trigger-popover"
      className={cn(
        "aui-composer-trigger-popover bg-popover text-popover-foreground absolute start-0 bottom-full z-50 mb-2 w-64 overflow-hidden rounded-xl border",
        className,
      )}
      isLoading={isLoading}
      {...props}
    >
      {directive ? (
        <ComposerPrimitive.TriggerPopover.Directive
          formatter={directive.formatter ?? defaultDirectiveFormatter}
          onInserted={directive.onInserted}
        />
      ) : action ? (
        <ComposerPrimitive.TriggerPopover.Action
          formatter={action.formatter ?? defaultDirectiveFormatter}
          onExecute={action.onExecute}
          removeOnExecute={action.removeOnExecute}
        />
      ) : null}
      <Items
        iconMap={iconMap}
        fallbackIcon={fallbackIcon}
        emptyLabel={emptyItemsLabel}
        loadingLabel={loadingLabel}
        isLoading={isLoading}
      />
      {children}
    </ComposerPrimitive.TriggerPopover>
  );
};
ComposerTriggerPopoverImpl.displayName = "ComposerTriggerPopover";

export const ComposerTriggerPopover = memo(
  ComposerTriggerPopoverImpl,
) as FC<ComposerTriggerPopoverProps>;
