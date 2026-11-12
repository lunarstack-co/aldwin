import type { TriggerItem } from "@assistant-ui/react";

import type { PickedApp } from "@/state/chat-store";
import type { ConnectorInfo } from "@/types/protocol";

/** The host uses at most this many picked apps per message, as with skills. */
export const MAX_PICKED_APPS = 3;

export const APP_ITEM_TYPE = "app";

/** An app the `/` menu can offer: connected, turned on, and with at least one tool. */
export type AppOption = PickedApp & { toolCount: number };

export function pickableApps(connectors: readonly ConnectorInfo[]): AppOption[] {
  return connectors
    .filter((c) => c.enabled && c.status === "connected" && c.tools.some((t) => t.effective !== "off"))
    .map((c) => ({ name: c.name, title: c.title, toolCount: c.tools.filter((t) => t.effective !== "off").length }));
}

/** Apps whose title or name starts with the query first, then those containing it. */
export function searchApps(apps: readonly AppOption[], query: string): AppOption[] {
  const q = query.trim().toLowerCase();
  const text = (app: AppOption) => `${app.title} ${app.name}`.toLowerCase();
  const starts = apps.filter((app) => app.title.toLowerCase().startsWith(q) || app.name.startsWith(q));
  const contains = apps.filter((app) => !starts.includes(app) && text(app).includes(q));
  return [...starts, ...contains];
}

export function toAppTriggerItem(app: AppOption, toolsLabel: (count: number) => string): TriggerItem {
  return {
    id: app.name,
    type: APP_ITEM_TYPE,
    label: app.title,
    description: toolsLabel(app.toolCount),
    metadata: { icon: app.name },
  };
}
