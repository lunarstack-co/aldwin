"use client";

import { CircleCheck, Loader2, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

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
  cancelSignIn,
  loadConnectors,
  removeConnector,
  setConnectorEnabled,
  signInConnector,
} from "@/utils/settings/actions";
import { fill } from "@/utils/text";

import { SignInStep } from "./connector-sign-in";
import { byTone, StatusPill, type Tone } from "./status";

function toneOf(connector: ConnectorInfo): Tone {
  if (connector.status === "connected") return "ok";
  return connector.status === "disabled" ? "off" : "attention";
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
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

function ConnectorRow({ connector, onAuthenticate }: { connector: ConnectorInfo; onAuthenticate: () => void }) {
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
        <span className="max-w-full min-w-0 truncate font-medium">{connector.title}</span>
        <StatusPill tone={toneOf(connector)}>{copy.status[connector.status]}</StatusPill>
        {connector.status === "connected" ? (
          <span className="text-muted-foreground text-xs">
            {count === 1 ? copy.oneTool : fill(copy.tools, { count })}
          </span>
        ) : null}
        <span className="ms-auto flex flex-wrap items-center justify-end gap-1">
          {connector.status === "needs_auth" ? (
            <Button
              variant="outline"
              size="sm"
              disabled={signingIn}
              onClick={onAuthenticate}
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

function AddByUrl({ onAdd, onCancel }: { onAdd: (target: AddTarget) => void; onCancel: () => void }) {
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
      <DialogFooter className="mt-2">
        <Button type="button" variant="outline" onClick={onCancel} data-testid="add-app-cancel" className="max-md:h-10">
          {common.cancel}
        </Button>
        <Button type="submit" disabled={url.trim() === ""} className="max-md:h-10">
          {copy.add}
        </Button>
      </DialogFooter>
    </form>
  );
}

/** Where the connect dialog is: the address form, working (with or without a sign-in), done, or failed. */
type Flow =
  | { step: "address" }
  | { step: "working"; id: number; title: string; url: string | null; retry: () => void }
  | { step: "done"; title: string; url: string | null }
  | { step: "failed"; title: string; message: string; retry: () => void };

function ConnectDialog({
  flow,
  onAddress,
  onCancel,
  onClose,
}: {
  flow: Flow;
  onAddress: (target: AddTarget) => void;
  onCancel: () => void;
  onClose: () => void;
}) {
  const signIn = useSettingsStore((s) => s.signIn);
  const connected = useSettingsStore((s) =>
    flow.step === "done" ? s.connectors.data.items.find((c) => c.url === flow.url) : undefined,
  );
  const signingIn = flow.step === "working" && signIn !== null;
  const title =
    flow.step === "address"
      ? copy.addTitle
      : signingIn
        ? fill(signInCopy.title, { title: signIn.title })
        : flow.step === "done"
          ? fill(copy.connectedTitle, { title: flow.title })
          : flow.step === "failed"
            ? fill(copy.failedTitle, { title: flow.title })
            : fill(copy.connectingTitle, { title: flow.title });
  // Closing while something is running cancels it, the same as the Cancel button.
  const dismiss = flow.step === "working" ? onCancel : onClose;

  return (
    <Dialog open onOpenChange={(open) => !open && dismiss()}>
      <DialogContent data-testid="connect-dialog" data-step={signingIn ? "sign-in" : flow.step} className="sm:max-w-md">
        <DialogTitle className="pe-6">{title}</DialogTitle>
        {flow.step === "address" ? (
          <>
            <DialogDescription>{copy.addLede}</DialogDescription>
            <AddByUrl onAdd={onAddress} onCancel={onClose} />
          </>
        ) : flow.step === "working" ? (
          <>
            {signingIn ? (
              <SignInStep signIn={signIn} />
            ) : (
              <DialogDescription className="flex items-center gap-2" data-testid="adding-app">
                <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />
                {copy.connecting}
              </DialogDescription>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={onCancel} data-testid="sign-in-cancel" className="max-md:h-10">
                {common.cancel}
              </Button>
            </DialogFooter>
          </>
        ) : flow.step === "done" ? (
          <>
            <DialogDescription className="flex items-center gap-2" data-testid="connect-done">
              <CircleCheck aria-hidden className="text-live size-4" />
              {connected && connected.tools.length > 0
                ? fill(connected.tools.length === 1 ? copy.connectedOneTool : copy.connectedTools, {
                    count: connected.tools.length,
                  })
                : copy.connectedBody}
            </DialogDescription>
            <DialogFooter>
              <Button onClick={onClose} data-testid="connect-close" className="max-md:h-10">
                {copy.done}
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogDescription role="alert" className="text-destructive" data-testid="connect-failed">
              {flow.message}
            </DialogDescription>
            <DialogFooter>
              <Button variant="outline" onClick={onClose} className="max-md:h-10">
                {copy.close}
              </Button>
              <Button onClick={flow.retry} data-testid="connect-retry" className="max-md:h-10">
                {copy.tryAgain}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Library({ items, searching, onAdd }: { items: CatalogItem[]; searching: boolean; onAdd: (target: AddTarget) => void }) {
  return (
    <div className="flex flex-col gap-5" data-testid="connector-catalog">
      {groupCatalog(items, searching).map((group) => (
        <section key={group.key} data-category={group.key} className="flex flex-col gap-2">
          {group.label ? (
            <h4 className="text-muted-foreground text-xs font-medium tracking-wide uppercase">{group.label}</h4>
          ) : null}
          <ul className="grid gap-2 @lg:grid-cols-2">
            {group.items.map((item) => (
              <li key={item.name} className="flex items-start gap-3 rounded-lg border p-3" data-catalog={item.name}>
                <BrandMark name={item.name} kind="connector" className="mt-0.5 size-5" />
                <span className="flex min-w-0 flex-1 flex-col gap-0.5 [overflow-wrap:anywhere]">
                  <span className="font-medium">{item.title}</span>
                  <span className="text-muted-foreground text-sm">{item.purpose}</span>
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onAdd({ url: item.url, item })}
                  aria-label={fill(copy.addNamed, { title: item.title })}
                  className="shrink-0 max-md:h-10"
                >
                  {copy.add}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function ToolsSection() {
  const session = useSession();
  const connectors = useConnectors();
  const signIn = useSettingsStore((s) => s.signIn);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [query, setQuery] = useState("");
  // Each run gets an id; a run that was cancelled or replaced is ignored when it answers.
  const runs = useRef(0);

  function connect(title: string, url: string | null, request: () => Promise<Outcome>) {
    const id = ++runs.current;
    const retry = () => connect(title, url, request);
    setFlow({ step: "working", id, title, url, retry });
    void request().then((outcome) => {
      if (runs.current !== id) return;
      if (outcome.ok) setFlow({ step: "done", title, url });
      else setFlow({ step: "failed", title, message: outcome.message, retry });
    });
  }

  // The host gives up on a sign-in nobody answered; the dialog says so instead of waiting on.
  useEffect(() => {
    if (signIn === null || flow?.step !== "working") return;
    const { title, retry } = flow;
    const timer = window.setTimeout(
      () => {
        runs.current += 1;
        useSettingsStore.getState().setSignIn(null);
        setFlow({ step: "failed", title, message: fill(signInCopy.expired, { title: signIn.title }), retry });
      },
      Math.max(0, signIn.expiresAt - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [signIn, flow]);

  function add(target: AddTarget) {
    connect(target.item?.title ?? hostOf(target.url), target.url, () =>
      addConnector(session, target.url, target.item),
    );
  }

  function cancel() {
    runs.current += 1;
    cancelSignIn(session);
    setFlow(null);
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
  const searching = query.trim() !== "";
  const needle = query.trim().toLowerCase();
  const connected = new Set(items.map((connector) => connector.name));
  const mine = searching ? items.filter((c) => `${c.title} ${c.name}`.toLowerCase().includes(needle)) : items;
  // Apps already connected sit under Your apps, so the library lists only the rest.
  const library = catalog.filter((item) => !item.added && !connected.has(item.name) && matches(item, query));
  const nothingFound = searching && mine.length === 0 && library.length === 0;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2 @md:flex-row">
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={copy.searchLabel}
          aria-label={copy.searchLabel}
          className="h-10 min-w-0 @md:flex-1"
        />
        <Button
          variant="outline"
          onClick={() => setFlow({ step: "address" })}
          data-testid="add-app-open"
          className="h-10 self-start @md:self-auto"
        >
          <Plus aria-hidden />
          {copy.addApp}
        </Button>
      </div>
      {nothingFound ? (
        <p className="text-muted-foreground text-sm" data-testid="no-match">
          {fill(copy.noMatch, { query: query.trim() })}
        </p>
      ) : null}
      {searching && mine.length === 0 ? null : (
        <section className="flex flex-col gap-2" data-testid="your-apps">
          <h3 className="text-sm font-medium">{copy.connected}</h3>
          {items.length === 0 ? (
            <p className="text-muted-foreground rounded-lg border border-dashed p-4 text-sm" data-testid="no-apps">
              {copy.none}
            </p>
          ) : (
            <>
              <p className="text-muted-foreground text-sm">{copy.toolsHint}</p>
              <ul className="flex flex-col gap-2" data-testid="connector-list">
                {byTone(mine, toneOf).map((connector) => (
                  <ConnectorRow
                    key={connector.name}
                    connector={connector}
                    onAuthenticate={() =>
                      connect(connector.title, connector.url, () => signInConnector(session, connector.name))
                    }
                  />
                ))}
              </ul>
            </>
          )}
        </section>
      )}
      {library.length > 0 ? (
        <section className="flex flex-col gap-3" data-testid="app-library">
          <div className="flex flex-col gap-1">
            <h3 className="text-sm font-medium">{copy.library}</h3>
            <p className="text-muted-foreground text-sm">{copy.libraryHint}</p>
          </div>
          <Library items={library} searching={searching} onAdd={add} />
        </section>
      ) : null}
      {flow !== null ? (
        <ConnectDialog flow={flow} onAddress={add} onCancel={cancel} onClose={() => setFlow(null)} />
      ) : null}
    </div>
  );
}
