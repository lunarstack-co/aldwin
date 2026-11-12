"use client";

import { ChevronDown } from "lucide-react";
import { useEffect, useMemo, useState, type ComponentType } from "react";

import { BrandMark } from "@/components/brand/brand-mark";
import copy from "@/data/chat/model-picker.json";
import providerCopy from "@/data/settings/providers.json";
import { useSession } from "@/providers/chat-session-provider";
import { useChatStore } from "@/state/chat-store";
import { useConnectionStore } from "@/state/connection-store";
import { useSettingsStore } from "@/state/settings-store";
import type { ProviderModels } from "@/types/protocol";
import { cn } from "@/utils/cn";
import { chooseModel, loadModels } from "@/utils/settings/actions";
import { fill } from "@/utils/text";

import type { ModelGroup } from "./model-picker-menu";

type Menu = ComponentType<Parameters<typeof import("./model-picker-menu").default>[0]>;

// The list (and cmdk) loads on the first hover, focus or tap, so the chat page does not carry it.
const loadMenu = () => import("./model-picker-menu").then((m) => m.default);

// The registry trigger's ghost and outline looks, so the button does not shift when the menu takes over.
const TRIGGER = {
  // On phones the arrow and wide padding would squeeze the model's name, so they go.
  composer:
    "hover:bg-accent hover:text-accent-foreground h-8 max-w-[50%] px-2.5 py-1.5 text-xs max-md:h-10 max-md:px-1.5 max-md:[&>svg:last-child]:hidden",
  settings: "border-input hover:bg-accent hover:text-accent-foreground h-9 border bg-transparent px-3 py-2 max-md:h-10",
};
const TRIGGER_BASE =
  "focus-visible:ring-ring/50 [&_svg]:pointer-events-none flex w-fit items-center justify-between gap-2 overflow-hidden rounded-md text-sm whitespace-nowrap transition-colors outline-none focus-visible:ring-1 disabled:cursor-not-allowed disabled:opacity-50 [&_svg]:shrink-0";

const providerOf = (ref: string) => ref.split("/")[0] ?? "";

function groupOf(provider: ProviderModels): ModelGroup {
  const ready = provider.status === "ok";
  return {
    provider,
    options: provider.models.map((model) => ({
      id: model.ref,
      name: model.title,
      icon: <BrandMark name={provider.name} kind="provider" />,
      keywords: [model.id, provider.title],
      ...(ready
        ? {}
        : {
            disabled: true,
            description: providerCopy.status[provider.status],
          }),
    })),
  };
}

/**
 * The model Aldwin answers with, grouped by provider. The list is asked for on first open;
 * a change is global, so the terminal and every window use the same model.
 */
export function ModelPicker({
  fallback = "",
  disabled = false,
  placement,
}: {
  /** The model the host reported before the list has loaded. */
  fallback?: string;
  disabled?: boolean;
  placement: "composer" | "settings";
}) {
  const session = useSession();
  const models = useSettingsStore((s) => s.models);
  const [open, setOpen] = useState(false);
  const [menu, setMenu] = useState<{ Menu: Menu } | null>(null);
  // The plain button stays until a click opens the menu, so a click is never split across two buttons.
  const [opened, setOpened] = useState(false);
  const current = models.data.current || fallback;

  // Providers with nothing to offer (no key, no models) are left out of the list.
  const groups = useMemo(
    () => models.data.providers.filter((p) => p.models.length > 0).map(groupOf),
    [models.data.providers],
  );
  const chosen = groups.flatMap((g) => g.options).find((option) => option.id === current);
  const title = chosen?.name ?? (current.split("/").slice(1).join("/") || copy.choose);
  const label = fill(copy.label, { model: title });
  const triggerClassName = cn(TRIGGER_BASE, TRIGGER[placement]);
  // The list loads once the chat has started (after history, like the search picker), so the button shows the
  // model's proper name from the start rather than its id.
  const ready = useConnectionStore((s) => s.phase === "open");
  const started = useChatStore((s) => s.sessionsLoaded);
  useEffect(() => {
    if (ready && started && models.status === "idle") void loadModels(session);
  }, [ready, started, models.status, session]);
  const naming = ready && (models.status === "idle" || models.status === "loading");

  const face = (
    <>
      {current !== "" ? <BrandMark name={providerOf(current)} kind="provider" className="size-3.5" /> : null}
      {/* While the list is on its way the raw id would flash and then change, so the name fades in instead.
          If the list cannot load, the id shows rather than nothing. */}
      <span className={cn("truncate font-medium transition-opacity", naming && "opacity-0")}>
        {title}
      </span>
    </>
  );

  // Fetched ahead of the click, so the button keeps its name and look as the menu opens.
  function prepare() {
    if (menu === null) void loadMenu().then((Menu) => setMenu({ Menu }));
    if (models.status === "idle" || models.status === "error") void loadModels(session);
  }

  function onOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      setOpened(true);
      prepare();
    }
  }

  async function choose(ref: string) {
    if (ref === current) return;
    const outcome = await chooseModel(session, ref);
    if (!outcome.ok) useChatStore.getState().pushToast(outcome.message);
  }

  if (menu === null || !opened) {
    return (
      <button
        type="button"
        disabled={disabled}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={false}
        data-testid={`model-picker-${placement}`}
        data-model={current}
        className={triggerClassName}
        onPointerEnter={prepare}
        onFocus={prepare}
        onClick={() => onOpenChange(true)}
      >
        {face}
        <ChevronDown aria-hidden className="size-4 opacity-50" />
      </button>
    );
  }

  return (
    <menu.Menu
      status={models.status}
      groups={groups}
      current={current}
      open={open}
      side={placement === "composer" ? "top" : "bottom"}
      trigger={{
        label,
        testId: `model-picker-${placement}`,
        disabled,
        children: face,
      }}
      triggerClassName={triggerClassName}
      variant={placement === "composer" ? "ghost" : "outline"}
      size={placement === "composer" ? "sm" : "default"}
      onOpenChange={onOpenChange}
      onChoose={(ref) => void choose(ref)}
      onRetry={() => void loadModels(session)}
    />
  );
}
