// What the fake host knows about settings: the REST view, models, search and connectors.
import type {
  CatalogItem,
  ConnectorInfo,
  ProviderModels,
  SearchProviderItem,
} from "../../src/types/protocol";

export type KeyRow = {
  kind: "provider" | "search";
  name: string;
  title: string;
  source: "env" | "file" | "missing";
  masked: string;
};

export type SettingsView = {
  model: string;
  check_for_updates: boolean;
  limits: Record<string, number>;
  search: { provider: string; searxng_url: string | null };
  keys: KeyRow[];
};

/** The minimums `LimitsConfig` enforces, so the fake refuses what the host would. */
export const LIMIT_MINIMUMS: Record<string, number> = {
  context_exchanges: 1,
  profile_chars: 100,
  notes_chars: 100,
  notes_days: 1,
  persona_chars: 100,
  max_steps: 1,
  web_searches: 1,
  connector_tools: 5,
};

export function settingsFixture(): SettingsView {
  return {
    model: "openai/gpt-4o",
    check_for_updates: true,
    limits: {
      context_exchanges: 20,
      profile_chars: 4000,
      notes_chars: 4000,
      notes_days: 7,
      persona_chars: 2000,
      max_steps: 15,
      web_searches: 5,
      connector_tools: 40,
    },
    search: { provider: "tavily", searxng_url: null },
    keys: [
      { kind: "provider", name: "openai", title: "OpenAI", source: "file", masked: "sk-…a1b2" },
      { kind: "provider", name: "anthropic", title: "Anthropic", source: "file", masked: "sk-…c3d4" },
      { kind: "provider", name: "mistral", title: "Mistral", source: "missing", masked: "" },
      { kind: "search", name: "tavily", title: "Tavily", source: "file", masked: "tvly-…e5f6" },
      { kind: "search", name: "brave", title: "Brave", source: "missing", masked: "" },
    ],
  };
}

function model(provider: string, id: string, title: string) {
  return { ref: `${provider}/${id}`, id, title, context_window: 128000, supports_tools: true };
}

export function modelsFixture(): { current: string; providers: ProviderModels[] } {
  return {
    current: "openai/gpt-4o",
    providers: [
      {
        name: "openai",
        title: "OpenAI",
        local: false,
        configured: true,
        status: "ok",
        models: [model("openai", "gpt-4o", "GPT-4o"), model("openai", "gpt-4o-mini", "GPT-4o mini")],
      },
      {
        name: "anthropic",
        title: "Anthropic",
        local: false,
        configured: true,
        status: "ok",
        models: [model("anthropic", "claude-sonnet-4", "Claude Sonnet 4")],
      },
      {
        name: "ollama",
        title: "Ollama",
        local: true,
        configured: true,
        status: "not_running",
        models: [model("ollama", "llama3.1", "Llama 3.1")],
      },
      { name: "mistral", title: "Mistral", local: false, configured: false, status: "not_configured", models: [] },
    ],
  };
}

function searchItem(name: string, title: string, extra: Partial<SearchProviderItem>): SearchProviderItem {
  return {
    name,
    title,
    kind: "hosted",
    connected: false,
    source: null,
    detail: "",
    active: false,
    signup_url: `https://${name}.example.com/signup`,
    check_note: null,
    env_var: null,
    ...extra,
  };
}

export function searchFixture(): { setting: string; active: string | null; items: SearchProviderItem[] } {
  return {
    setting: "tavily",
    active: "tavily",
    items: [
      searchItem("tavily", "Tavily", { connected: true, source: "file", active: true, detail: "tvly-…e5f6" }),
      searchItem("brave", "Brave", {}),
      searchItem("searxng", "SearXNG", { kind: "local", detail: "Not running at http://127.0.0.1:8888" }),
    ],
  };
}

function tool(name: string, description = "") {
  return { name, model_name: `mcp__github__${name}`, title: null, description, read_only: true, setting: null, effective: "auto" as const };
}

/** GitHub's tools: three with real names for the `/` menu, then plain ones up to twelve. */
const GITHUB_TOOLS = [
  tool("create_issue", "Create a new issue in a repository."),
  tool("list_issues", "List issues in a repository."),
  tool("search_code", "Search code across repositories."),
  ...Array.from({ length: 9 }, (_, i) => tool(`tool_${i + 4}`)),
];

export function connectorFixture(name: string, title: string, extra: Partial<ConnectorInfo> = {}): ConnectorInfo {
  return {
    name,
    title,
    url: `https://mcp.${name}.example.com/mcp`,
    enabled: true,
    status: "connected",
    auth: "oauth",
    trust_read_only_hints: true,
    tools: [],
    error: null,
    retry_in_s: null,
    transport: "http",
    command: null,
    note: null,
    ...extra,
  };
}

export function connectorsFixture(): { items: ConnectorInfo[]; catalog: CatalogItem[] } {
  const catalogItem = (name: string, title: string): CatalogItem => ({
    name,
    title,
    purpose: `Work with ${title}.`,
    url: `https://mcp.${name}.example.com/mcp`,
    auth: "oauth",
    docs_url: `https://${name}.example.com/docs`,
    keywords: [],
    token_help_url: null,
    added: false,
  });
  return {
    items: [
      connectorFixture("github", "GitHub", { tools: GITHUB_TOOLS }),
      connectorFixture("notion", "Notion", { status: "needs_auth" }),
    ],
    catalog: [catalogItem("linear", "Linear"), catalogItem("dropbox", "Dropbox")],
  };
}

export const SKILL_BODIES: Record<string, string> = {
  "ship-notes": "# Ship notes\n\nWrite release notes in our house style: short, plain, newest first.",
};
