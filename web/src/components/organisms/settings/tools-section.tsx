"use client";

import { Plus } from "lucide-react";
import { useCallback, useState, type FormEvent } from "react";

import { BrandMark } from "@/components/brand/brand-mark";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { Field, hintId } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import common from "@/data/settings/common.json";
import signInCopy from "@/data/settings/sign-in.json";
import copy from "@/data/settings/tools.json";
import { useConnectors } from "@/hooks/use-settings-data";
import { useUnsavedChanges } from "@/hooks/use-unsaved-changes";
import { useSession } from "@/providers/chat-session-provider";
import { useSettingsStore } from "@/state/settings-store";
import type { CatalogItem, ConnectorInfo } from "@/types/protocol";
import type { Outcome } from "@/utils/ws/session";
import {
  addConnector,
  loadConnectors,
  removeConnector,
  setConnectorEnabled,
  signInConnector,
} from "@/utils/settings/actions";
import { fill } from "@/utils/text";

import { ConnectorSignIn } from "./connector-sign-in";
import { byTone, StatusPill, type Tone } from "./status";

function toneOf(connector: ConnectorInfo): Tone {
  if (connector.status === "connected") return "ok";
  return connector.status === "disabled" ? "off" : "attention";
}

function isWebAddress(text: string): boolean {
  try {
    const url = new URL(text);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

/** Runs a host request for one row and keeps its message, if it failed. */
function useRowAction(): {
  problem: string | null;
  run: (action: () => Promise<Outcome>) => Promise<boolean>;
} {
  const [problem, setProblem] = useState<string | null>(null);
  const run = useCallback(async (action: () => Promise<Outcome>) => {
    setProblem(null);
    const outcome = await action();
    // A dropped request is asked again by the list reload; the row stays quiet.
    if (!outcome.ok && outcome.code !== "dropped") setProblem(outcome.message);
    return outcome.ok;
  }, []);
  return { problem, run };
}

function RemoveDialog({ connector, onClose }: { connector: ConnectorInfo; onClose: () => void }) {
  const session = useSession();
  const { problem, run } = useRowAction();
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent data-testid="remove-connector">
        <DialogTitle>{fill(copy.removeTitle, { title: connector.title })}</DialogTitle>
        <DialogDescription>{copy.removeBody}</DialogDescription>
        {problem !== null ? (
          <p role="alert" className="text-destructive text-sm">
            {problem}
          </p>
        ) : null}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="max-md:h-10">
            {common.cancel}
          </Button>
          <Button
            onClick={() => void run(() => removeConnector(session, connector.name)).then((ok) => ok && onClose())}
            className="max-md:h-10"
          >
            {copy.remove}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ConnectorRow({ connector }: { connector: ConnectorInfo }) {
  const session = useSession();
  const { problem, run } = useRowAction();
  const signingIn = useSettingsStore((s) => s.signIn?.name === connector.name);
  const [removing, setRemoving] = useState(false);
  const count = connector.tools.length;

  return (
    <li
      className="flex flex-col gap-2 rounded-lg border p-3"
      data-connector={connector.name}
      data-status={connector.status}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <BrandMark name={connector.name} kind="connector" className="size-5" />
        <span className="min-w-0 truncate font-medium">{connector.title}</span>
        <StatusPill tone={toneOf(connector)}>{copy.status[connector.status]}</StatusPill>
        {connector.status === "connected" ? (
          <span className="text-muted-foreground text-xs">
            {count === 1 ? copy.oneTool : fill(copy.tools, { count })}
          </span>
        ) : null}
        <span className="ms-auto flex items-center gap-1">
          {connector.status === "needs_auth" ? (
            <Button
              variant="outline"
              size="sm"
              disabled={signingIn}
              onClick={() => void run(() => signInConnector(session, connector.name))}
              className="max-md:h-10"
            >
              {copy.authenticate}
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => setRemoving(true)} className="max-md:h-10">
            {copy.remove}
          </Button>
          <label className="flex items-center max-md:min-h-10 max-md:px-1" title={fill(copy.useHint, { title: connector.title })}>
            <Switch
              checked={connector.enabled}
              onCheckedChange={(enabled) => void run(() => setConnectorEnabled(session, connector.name, enabled))}
              aria-label={fill(copy.use, { title: connector.title })}
              data-testid={`connector-enabled-${connector.name}`}
            />
          </label>
        </span>
      </div>
      {connector.error !== null ? <p className="text-muted-foreground text-xs">{connector.error}</p> : null}
      {problem !== null ? (
        <p role="alert" className="text-destructive text-sm">
          {problem}
        </p>
      ) : null}
      {removing ? <RemoveDialog connector={connector} onClose={() => setRemoving(false)} /> : null}
    </li>
  );
}

type AddTarget = { url: string; item?: CatalogItem };

function matches(item: CatalogItem, query: string): boolean {
  const words = [item.title, item.name, item.purpose, ...item.keywords].join(" ").toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => words.includes(word));
}

type CatalogGroup = { key: string; label: string | null; items: CatalogItem[] };

/** Apps under their category headings in catalogue order; one unlabelled group while searching or for older hosts. */
export function groupCatalog(items: CatalogItem[], searching: boolean): CatalogGroup[] {
  if (searching || !items.some((item) => item.category)) return [{ key: "all", label: null, items }];
  const labels = copy.categories as Record<string, string>;
  const groups: CatalogGroup[] = [];
  for (const item of items) {
    const key = item.category ?? "other";
    let group = groups.find((g) => g.key === key);
    if (!group) {
      group = { key, label: labels[key] ?? labels.other ?? key, items: [] };
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}

function AddByUrl({ onAdd }: { onAdd: (target: AddTarget) => void }) {
  const [url, setUrl] = useState("");
  useUnsavedChanges(url.trim() !== "");
  const [invalid, setInvalid] = useState(false);
  const id = "connector-url";
  const error = invalid ? copy.badUrl : undefined;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const address = url.trim();
    setInvalid(!isWebAddress(address));
    if (isWebAddress(address)) onAdd({ url: address });
  }

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-2" data-testid="add-connector-url">
      <Field id={id} label={copy.urlLabel} hint={copy.urlHint} error={error}>
        <Input
          id={id}
          type="url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          aria-describedby={hintId(id, copy.urlHint, error)}
          aria-invalid={error !== undefined || undefined}
          className="max-md:h-10"
        />
      </Field>
      <Button type="submit" size="sm" variant="outline" disabled={url.trim() === ""} className="self-start max-md:h-10">
        {copy.add}
      </Button>
    </form>
  );
}

function AddAppDialog({
  catalog,
  connected,
  onAdd,
  onClose,
}: {
  catalog: CatalogItem[];
  connected: Set<string>;
  onAdd: (target: AddTarget) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const shown = catalog.filter((item) => matches(item, query));
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent data-testid="add-app" className="flex max-h-[min(40rem,90dvh)] flex-col gap-4 sm:max-w-lg">
        <DialogTitle>{copy.addTitle}</DialogTitle>
        <DialogDescription>{copy.addLede}</DialogDescription>
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={copy.searchLabel}
          aria-label={copy.searchLabel}
          className="h-11 text-base"
        />
        <div className="-mx-1 flex min-h-0 flex-col gap-4 overflow-y-auto px-1">
          {shown.length === 0 ? (
            <p className="text-muted-foreground text-sm">{fill(copy.noMatch, { query: query.trim() })}</p>
          ) : (
            <div className="flex flex-col gap-4" data-testid="connector-catalog">
              {groupCatalog(shown, query.trim() !== "").map((group) => (
                <section key={group.key} data-category={group.key} className="flex flex-col gap-2">
                  {group.label ? (
                    <h3 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">{group.label}</h3>
                  ) : null}
                  <ul className="flex flex-col gap-2">
                    {group.items.map((item) => {
                      const added = item.added || connected.has(item.name);
                      return (
                        <li key={item.name} className="flex items-start gap-3 rounded-lg border p-3" data-catalog={item.name}>
                          <BrandMark name={item.name} kind="connector" className="mt-0.5 size-5" />
                          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <span className="font-medium">{item.title}</span>
                            <span className="text-muted-foreground text-sm">{item.purpose}</span>
                          </span>
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={added}
                            onClick={() => onAdd({ url: item.url, item })}
                            className="shrink-0 max-md:h-10"
                          >
                            {added ? copy.added : copy.add}
                          </Button>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))}
            </div>
          )}
          <div className="flex flex-col gap-2 border-t pt-4">
            <h3 className="text-sm font-medium">{copy.addUrl}</h3>
            <AddByUrl onAdd={onAdd} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="add-app-cancel" className="max-md:h-10">
            {common.cancel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ToolsSection() {
  const session = useSession();
  const connectors = useConnectors();
  const [expired, setExpired] = useState<string | null>(null);
  const onExpired = useCallback((title: string) => setExpired(title), []);
  const [picking, setPicking] = useState(false);
  const [adding, setAdding] = useState<string | null>(null);
  const { problem, run } = useRowAction();

  // The dialog closes at once; a sign-in, if the app needs one, shows on the page.
  async function add(target: AddTarget) {
    setPicking(false);
    setExpired(null);
    setAdding(target.item?.title ?? target.url);
    await run(() => addConnector(session, target.url, target.item));
    setAdding(null);
  }

  if (connectors.status === "error") {
    return (
      <Callout type="error" title={copy.loadError}>
        <Button variant="outline" size="sm" onClick={() => void loadConnectors(session)}>
          {common.retry}
        </Button>
      </Callout>
    );
  }
  if (connectors.status !== "ready") return <p className="text-muted-foreground text-sm">{common.loading}</p>;

  const { items, catalog } = connectors.data;
  const addButton = (
    <Button onClick={() => setPicking(true)} data-testid="add-app-open" className="max-md:h-10">
      <Plus aria-hidden />
      {copy.addApp}
    </Button>
  );
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-medium">{copy.connected}</h3>
        {addButton}
      </div>
      <ConnectorSignIn onExpired={onExpired} />
      {expired !== null ? (
        <p role="status" className="text-sm" data-testid="sign-in-expired">
          {fill(signInCopy.expired, { title: expired })}
        </p>
      ) : null}
      {adding !== null ? (
        <p role="status" className="text-muted-foreground text-sm" data-testid="adding-app">
          {fill(copy.adding, { title: adding })}
        </p>
      ) : null}
      {problem !== null ? (
        <p role="alert" className="text-destructive text-sm">
          {problem}
        </p>
      ) : null}
      {items.length === 0 ? (
        <div className="flex flex-col items-start gap-3 rounded-lg border border-dashed p-6" data-testid="no-apps">
          <p className="text-sm">{copy.none}</p>
        </div>
      ) : (
        <>
          <p className="text-muted-foreground text-sm">{copy.toolsHint}</p>
          <ul className="flex flex-col gap-2" data-testid="connector-list">
            {byTone(items, toneOf).map((connector) => (
              <ConnectorRow key={connector.name} connector={connector} />
            ))}
          </ul>
        </>
      )}
      {picking ? (
        <AddAppDialog
          catalog={catalog}
          connected={new Set(items.map((connector) => connector.name))}
          onAdd={(target) => void add(target)}
          onClose={() => setPicking(false)}
        />
      ) : null}
    </div>
  );
}
