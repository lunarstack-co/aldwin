"use client";

import { type TriggerAdapter, type TriggerItem, type TriggerMatcher } from "@assistant-ui/react";
import { Blocks, Settings } from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo, type FC, type MouseEvent } from "react";

import { ComposerTriggerPopover } from "@/components/assistant-ui/elements/composer-trigger-popover.aui";
import { BrandMark } from "@/components/brand/brand-mark";
import { Button, buttonVariants } from "@/components/ui/button";
import { settingsHref } from "@/constants/routes";
import copy from "@/data/chat/apps-menu.json";
import { useSession } from "@/providers/chat-session-provider";
import { useChatStore, type PickedApp } from "@/state/chat-store";
import { useSettingsStore } from "@/state/settings-store";
import { MAX_PICKED_APPS, pickableApps, searchApps, toAppTriggerItem } from "@/utils/apps";
import { cn } from "@/utils/cn";
import { loadConnectors } from "@/utils/settings/actions";
import { fill } from "@/utils/text";

import { CloseOnOutsidePointer } from "./skills-popover";

const keepFocus = (event: MouseEvent) => event.preventDefault();

// A `/` at the start or after whitespace, then an app-like word up to the caret.
const APP_QUERY = /(?:^|\s)\/([A-Za-z0-9 _-]*)$/;
const toolsLabel = (count: number) => (count === 1 ? copy.oneTool : fill(copy.tools, { count }));

/** Adds an app chip for the next message; past the limit it says so instead. */
export function addPickedApp(app: PickedApp): void {
  const store = useChatStore.getState();
  if (store.pickedApps.length >= MAX_PICKED_APPS && !store.pickedApps.some((a) => a.name === app.name)) {
    store.pushToast(copy.limit);
    return;
  }
  store.pickApp({ name: app.name, title: app.title });
}

export function AppsPopover() {
  const session = useSession();
  const connectors = useSettingsStore((s) => s.connectors);
  const apps = useMemo(() => pickableApps(connectors.data.items), [connectors.data.items]);

  const adapter = useMemo<TriggerAdapter>(
    () => ({
      categories: () => [],
      categoryItems: () => [],
      search: (query) =>
        connectors.status === "ready" ? searchApps(apps, query).map((app) => toAppTriggerItem(app, toolsLabel)) : [],
    }),
    [connectors.status, apps],
  );

  const iconMap = useMemo(() => {
    const map: Record<string, FC<{ className?: string }>> = {};
    for (const app of apps) {
      map[app.name] = ({ className }) => <BrandMark name={app.name} kind="connector" className={className} />;
    }
    return map;
  }, [apps]);

  // `/` opens the menu only for an app-like word that some app matches, so `/etc/hosts` and `/new` stay
  // plain text and Enter sends them. A bare `/` always opens it.
  const matcher = useCallback<TriggerMatcher>(
    (text, _char, cursor) => {
      const found = APP_QUERY.exec(text.slice(0, cursor));
      if (!found) return null;
      const query = found[1];
      const known = connectors.status === "ready";
      if (query !== "" && known && searchApps(apps, query).length === 0) return null;
      return { query, offset: cursor - query.length - 1, endOffset: cursor };
    },
    [apps, connectors.status],
  );

  function pick(item: TriggerItem) {
    const app = apps.find((a) => a.name === item.id);
    if (app) addPickedApp(app);
  }

  const failed = connectors.status === "error";
  const empty = failed ? copy.error : apps.length === 0 ? copy.none : copy.noMatch;

  return (
    <div
      data-testid="apps-menu"
      className="bg-popover text-popover-foreground absolute start-0 bottom-full z-50 mb-2 hidden w-[min(20rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-xl border shadow-md has-[[role=listbox]]:flex"
    >
      <ComposerTriggerPopover
        char="/"
        matcher={matcher}
        adapter={adapter}
        action={{ onExecute: pick, removeOnExecute: true }}
        iconMap={iconMap}
        fallbackIcon={Blocks}
        isLoading={connectors.status === "idle" || connectors.status === "loading"}
        loadingLabel={copy.loading}
        emptyItemsLabel={empty}
        aria-label={copy.label}
        className="static m-0 max-h-[min(20rem,45dvh)] w-full overflow-y-auto rounded-none border-0 max-md:[&_[role=option]]:min-h-10"
      >
        <CloseOnOutsidePointer />
      </ComposerTriggerPopover>
      <div className="flex flex-col gap-1 border-t p-1">
        {failed ? (
          <Button
            variant="outline"
            size="sm"
            onMouseDown={keepFocus}
            onClick={() => void loadConnectors(session)}
            data-testid="apps-retry"
            className="max-md:h-10"
          >
            {copy.retry}
          </Button>
        ) : null}
        <Link
          href={settingsHref("tools")}
          className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "w-full justify-start max-md:h-10")}
          data-testid="apps-manage"
        >
          <Settings aria-hidden />
          {copy.manage}
        </Link>
      </div>
    </div>
  );
}
