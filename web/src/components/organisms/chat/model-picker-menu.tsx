"use client";

import { Settings } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import {
  ModelSelectorContent,
  ModelSelectorEmpty,
  ModelSelectorGroup,
  ModelSelectorItem,
  ModelSelectorList,
  ModelSelectorRoot,
  ModelSelectorSearch,
  ModelSelectorTrigger,
  type ModelOption,
} from "@/components/assistant-ui/elements/model-selector";
import { Button, buttonVariants } from "@/components/ui/button";
import { settingsHref } from "@/constants/routes";
import copy from "@/data/chat/model-picker.json";
import common from "@/data/settings/common.json";
import type { LoadStatus } from "@/state/settings-store";
import type { ProviderModels } from "@/types/protocol";
import { cn } from "@/utils/cn";

export type ModelGroup = { provider: ProviderModels; options: ModelOption[] };

/** The open model list, loaded with `cmdk` only once someone opens the picker. */
export default function ModelPickerMenu({
  status,
  groups,
  current,
  open,
  side,
  trigger,
  triggerClassName,
  variant,
  size,
  onOpenChange,
  onChoose,
  onRetry,
}: {
  status: LoadStatus;
  groups: ModelGroup[];
  current: string;
  open: boolean;
  side: "top" | "bottom";
  trigger: {
    label: string;
    testId: string;
    disabled: boolean;
    children: ReactNode;
  };
  triggerClassName: string;
  variant: "ghost" | "outline";
  size: "sm" | "default";
  onOpenChange: (open: boolean) => void;
  onChoose: (ref: string) => void;
  onRetry: () => void;
}) {
  const options = groups.flatMap((group) => group.options);
  return (
    <ModelSelectorRoot
      models={options}
      value={current}
      onValueChange={onChoose}
      open={open}
      onOpenChange={onOpenChange}
    >
      <ModelSelectorTrigger
        disabled={trigger.disabled}
        variant={variant}
        size={size}
        aria-label={trigger.label}
        data-testid={trigger.testId}
        data-model={current}
        className={triggerClassName}
      >
        {trigger.children}
      </ModelSelectorTrigger>
      <ModelSelectorContent side={side} data-testid="model-picker-menu">
        {status === "error" ? (
          <div role="alert" className="flex flex-col items-start gap-2 p-3 text-sm">
            {copy.loadError}
            <Button variant="outline" size="sm" onClick={onRetry}>
              {common.retry}
            </Button>
          </div>
        ) : status !== "ready" ? (
          <p className="text-muted-foreground p-3 text-sm">{copy.loading}</p>
        ) : groups.every((group) => !group.provider.configured) ? (
          <div className="flex flex-col items-start gap-2 p-3 text-sm">
            {copy.none}
            <Link
              href={settingsHref("providers")}
              onClick={() => onOpenChange(false)}
              className={buttonVariants({ variant: "outline", size: "sm" })}
              data-testid="model-picker-add-provider"
            >
              {copy.addProvider}
            </Link>
          </div>
        ) : (
          <>
            <ModelSelectorSearch placeholder={copy.search} aria-label={copy.search} />
            <ModelSelectorList>
              <ModelSelectorEmpty>{copy.empty}</ModelSelectorEmpty>
              {groups.map(({ provider, options: items }) => (
                <ModelSelectorGroup key={provider.name} heading={provider.title} data-provider={provider.name}>
                  {items.map((model) => (
                    <ModelSelectorItem
                      key={model.id}
                      model={model}
                      data-model={model.id}
                      data-current={model.id === current || undefined}
                      // The model in use is the one that stands out; other rows only tint under the pointer.
                      className="data-current:bg-muted data-current:font-medium"
                    />
                  ))}
                </ModelSelectorGroup>
              ))}
            </ModelSelectorList>
          </>
        )}
        <div className="border-t p-1">
          <Link
            href={settingsHref("providers")}
            onClick={() => onOpenChange(false)}
            className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "w-full justify-start max-md:h-10")}
            data-testid="model-picker-manage"
          >
            <Settings aria-hidden />
            {copy.manage}
          </Link>
        </div>
      </ModelSelectorContent>
    </ModelSelectorRoot>
  );
}
