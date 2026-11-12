import { useChatStore } from "@/state/chat-store";
import { useSettingsStore, type SignIn } from "@/state/settings-store";
import type { CatalogItem, Event } from "@/types/protocol";
import type { ChatSession, Outcome, RequestBody } from "@/utils/ws/session";

const store = () => useSettingsStore.getState();

export async function loadModels(session: ChatSession, refresh = false): Promise<Outcome> {
  store().setModelsStatus("loading");
  const outcome = await session.request({ type: "models.list", ...(refresh ? { refresh: true } : {}) }, (event) => {
    if (event.kind === "models") store().setModels({ current: event.current, providers: event.providers });
  });
  if (!outcome.ok) store().setModelsStatus("error");
  return outcome;
}

/** Changes the model everywhere: the composer, Settings and the terminal read the same setting. */
export async function chooseModel(session: ChatSession, ref: string): Promise<Outcome> {
  const outcome = await session.request({ type: "set_model", name: ref });
  if (outcome.ok) store().setCurrentModel(ref);
  return outcome;
}

// The host answers most changes with `done` alone, so each change asks for its list again when it settles.
async function thenRefresh(outcome: Promise<Outcome>, refresh: () => void): Promise<Outcome> {
  const settled = await outcome;
  // After a drop the reconnect asks for every list itself.
  if (settled.ok || settled.code !== "dropped") refresh();
  return settled;
}

export function addServer(
  session: ChatSession,
  server: { name: string; baseUrl: string; key?: string; local: boolean },
): Promise<Outcome> {
  return thenRefresh(
    session.request({
      type: "provider.server",
      name: server.name,
      base_url: server.baseUrl,
      local: server.local,
      ...(server.key ? { key: server.key } : {}),
    }),
    () => session.refreshModels(),
  );
}

export function disconnectProvider(session: ChatSession, provider: string): Promise<Outcome> {
  return thenRefresh(session.request({ type: "provider.disconnect", provider }), () => session.refreshModels());
}

export async function loadSearch(session: ChatSession): Promise<Outcome> {
  store().setSearchStatus("loading");
  const outcome = await session.request({ type: "search.list" }, (event) => {
    if (event.kind === "search.providers")
      store().setSearch({ setting: event.setting, active: event.active, items: event.items });
  });
  if (!outcome.ok) store().setSearchStatus("error");
  return outcome;
}

export function connectSearch(session: ChatSession, provider: string, key?: string): Promise<Outcome> {
  return thenRefresh(session.request({ type: "search.connect", provider, ...(key ? { key } : {}) }), () =>
    session.refreshSearch(),
  );
}

/** Shows the choice at once, then reads the host's list to confirm it (or put it back). */
export function makeSearchActive(session: ChatSession, provider: string): Promise<Outcome> {
  const { search } = store();
  store().setSearch({
    ...search.data,
    setting: provider,
    active: provider,
    items: search.data.items.map((item) => ({ ...item, active: item.name === provider })),
  });
  return thenRefresh(session.request({ type: "search.use", provider }), () => session.refreshSearch());
}

export function disconnectSearch(session: ChatSession, provider: string): Promise<Outcome> {
  return thenRefresh(session.request({ type: "search.disconnect", provider }), () => session.refreshSearch());
}

export async function showSkill(session: ChatSession, name: string): Promise<Outcome> {
  return session.request({ type: "skills.show", name }, (event) => {
    if (event.kind === "skill.body") store().setSkillBody({ name: event.name, body: event.body });
  });
}

export async function loadConnectors(session: ChatSession): Promise<Outcome> {
  store().setConnectorsStatus("loading");
  const outcome = await session.request({ type: "connectors.list" }, (event) => {
    if (event.kind === "connectors") store().setConnectors({ items: event.items, catalog: event.catalog });
  });
  if (!outcome.ok) store().setConnectorsStatus("error");
  return outcome;
}

// A sign-in the host asks for while adding or signing in a connector; the page opens its address only on a click.
function onSignIn(event: Event) {
  if (event.kind !== "connector.auth") return;
  const signIn: SignIn = {
    name: event.name,
    title: event.title,
    mode: event.mode,
    url: event.url,
    tokenHelpUrl: event.token_help_url,
    expiresAt: Date.now() + event.expires_in * 1000,
  };
  store().setSignIn(signIn);
}

async function withSignIn(session: ChatSession, body: RequestBody): Promise<Outcome> {
  const outcome = await thenRefresh(session.request(body, onSignIn), () => session.refreshConnectors());
  store().setSignIn(null);
  return outcome;
}

export function addConnector(session: ChatSession, url: string, from?: CatalogItem): Promise<Outcome> {
  return withSignIn(session, {
    type: "connectors.add",
    url,
    auth: from?.auth ?? "none",
    no_browser: true,
    ...(from ? { name: from.name, title: from.title } : {}),
  });
}

export function signInConnector(session: ChatSession, name: string): Promise<Outcome> {
  return withSignIn(session, { type: "connectors.login", name, no_browser: true });
}

export function setConnectorEnabled(session: ChatSession, name: string, enabled: boolean): Promise<Outcome> {
  return thenRefresh(session.request({ type: enabled ? "connectors.enable" : "connectors.disable", name }), () =>
    session.refreshConnectors(),
  );
}

export function removeConnector(session: ChatSession, name: string): Promise<Outcome> {
  return thenRefresh(session.request({ type: "connectors.remove", name }), () => session.refreshConnectors());
}

export function pasteSignIn(session: ChatSession, name: string, redirectUrl: string): Promise<Outcome> {
  return session.request({ type: "connectors.auth_paste", name, redirect_url: redirectUrl });
}

export function tokenSignIn(session: ChatSession, name: string, token: string): Promise<Outcome> {
  return session.request({ type: "connectors.auth_token", name, token });
}

/**
 * Ends a sign-in the host is waiting on. The host's `stop` ends it, but `stop`
 * also ends an answer in progress, so it is sent only when none is running;
 * otherwise the panel closes and the host's own timeout ends the wait.
 */
export function cancelSignIn(session: ChatSession): void {
  store().setSignIn(null);
  if (useChatStore.getState().activeId === null) void session.request({ type: "stop" });
}
