import { create } from "zustand";

import type {
  AuthMode,
  CatalogItem,
  ConnectorInfo,
  ConnectorStatusEvent,
  ProviderModels,
  SearchProviderItem,
} from "@/types/protocol";

export type LoadStatus = "idle" | "loading" | "ready" | "error";

/** A list the host owns: asked for once, then kept current by its events. */
export type Loaded<T> = { status: LoadStatus; data: T };

export type ModelsData = { current: string; providers: ProviderModels[] };
export type SearchData = {
  setting: string;
  active: string | null;
  items: SearchProviderItem[];
};
export type ConnectorsData = { items: ConnectorInfo[]; catalog: CatalogItem[] };

/** A connector sign-in the host is waiting on; `expiresAt` is a time in milliseconds. */
export type SignIn = {
  name: string;
  title: string;
  mode: AuthMode;
  url: string | null;
  tokenHelpUrl: string | null;
  expiresAt: number;
};

type SettingsState = {
  models: Loaded<ModelsData>;
  search: Loaded<SearchData>;
  connectors: Loaded<ConnectorsData>;
  signIn: SignIn | null;
  skillBody: { name: string; body: string } | null;
  setModelsStatus: (status: LoadStatus) => void;
  setModels: (data: ModelsData) => void;
  setCurrentModel: (current: string) => void;
  setSearchStatus: (status: LoadStatus) => void;
  setSearch: (data: SearchData) => void;
  setConnectorsStatus: (status: LoadStatus) => void;
  setConnectors: (data: ConnectorsData) => void;
  applyConnectorStatus: (event: ConnectorStatusEvent) => void;
  setSignIn: (signIn: SignIn | null) => void;
  setSkillBody: (skillBody: { name: string; body: string } | null) => void;
  /** After a drop, requests in flight are lost; lists that were loading are asked again. */
  forgetLoading: () => void;
};

const idle = <T>(data: T): Loaded<T> => ({ status: "idle", data });

export const useSettingsStore = create<SettingsState>((set) => ({
  models: idle<ModelsData>({ current: "", providers: [] }),
  search: idle<SearchData>({ setting: "", active: null, items: [] }),
  connectors: idle<ConnectorsData>({ items: [], catalog: [] }),
  signIn: null,
  skillBody: null,

  setModelsStatus: (status) => set((s) => ({ models: { ...s.models, status } })),
  setModels: (data) => set({ models: { status: "ready", data } }),
  setCurrentModel: (current) =>
    set((s) => ({
      models: { ...s.models, data: { ...s.models.data, current } },
    })),
  setSearchStatus: (status) => set((s) => ({ search: { ...s.search, status } })),
  setSearch: (data) => set({ search: { status: "ready", data } }),
  setConnectorsStatus: (status) => set((s) => ({ connectors: { ...s.connectors, status } })),
  setConnectors: (data) => set({ connectors: { status: "ready", data } }),
  // A disabled connector is the only one that is off; any other status means it is on.
  applyConnectorStatus: (event) =>
    set((s) => ({
      connectors: {
        ...s.connectors,
        data: {
          ...s.connectors.data,
          items: s.connectors.data.items.map((item) =>
            item.name === event.name
              ? {
                  ...item,
                  enabled: event.status !== "disabled",
                  status: event.status,
                  error: event.error,
                  retry_in_s: event.retry_in_s,
                }
              : item,
          ),
        },
      },
    })),
  setSignIn: (signIn) => set({ signIn }),
  setSkillBody: (skillBody) => set({ skillBody }),
  forgetLoading: () =>
    set((s) => ({
      models: s.models.status === "loading" ? { ...s.models, status: "idle" } : s.models,
      search: s.search.status === "loading" ? { ...s.search, status: "idle" } : s.search,
      connectors: s.connectors.status === "loading" ? { ...s.connectors, status: "idle" } : s.connectors,
    })),
}));
