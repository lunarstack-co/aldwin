# Data and privacy

Aldwin collects no analytics or telemetry. Everything it stores lives in one folder on your computer.

## What leaves your computer

- **To OpenAI or other cloud model providers:** when using a cloud model, your prompts, conversation exchanges, your profile and daily notes (within character caps), and the contents of any file, document, or page Aldwin reads for the turn.
- **To search providers and websites:** when `web_search` runs, the query goes to the configured backend: a self-hosted SearXNG instance, or Tavily, Brave, Exa, Perplexity, Parallel, or Firecrawl when you connected that provider. A hosted provider receives the query text and handles it under its own privacy terms. When you connect a provider with `/search` (or `/model` for model keys), Aldwin sends the key to that provider once to check it, and saves it only if the provider accepts it. Prefer a SearXNG instance you run yourself. SearXNG may be a localhost or private URL; that is intentional and is not blocked as SSRF. JSON output must be enabled on the instance (`format=json` / `search.formats`). When `web_fetch` runs, HTTP GET requests go to the target URL.
- **To PyPI:** on launch, Aldwin may fetch public project metadata from `https://pypi.org/pypi/aldwin/json` to tell you an update is available. No user id, keys, or home path are sent. Set `check_for_updates: false` in `config.yaml` to turn this off.

Everything else under `~/.aldwin` stays on your computer, including your API keys, the approval log, the memory index, and saved conversations.

## Fully local

When you use a local model on a loopback address (`127.0.0.1`, `localhost`, `::1`), such as Ollama or LM Studio:

- **Zero conversation data leaves your machine:** prompts, messages, memory, and file contents are sent strictly over loopback to the model server running on your computer.
- **Outbound network features:**
  - `web_search`: sends search queries to the configured provider (SearXNG URL and/or a hosted API key) only when set up and called. See [Set up SearXNG](searxng.md).
  - `web_fetch` and `download_file`: make HTTP GET requests to target URLs only when invoked.
  - Remote MCP connectors: talk to their configured remote server URLs.
  - PyPI update check: set `check_for_updates: false` in `~/.aldwin/config.yaml` to disable the startup version check.
- When `check_for_updates: false` is set and web tools or remote connectors are not used, Aldwin operates in a complete air-gapped, zero-outbound-network posture.

Custom and overridden servers live under `providers:` in `config.yaml` (`shape`, `local`, `base_url`, `secret`, `title`). See the README [Other server](https://github.com/lunarstack-co/aldwin/blob/main/README.md#run-it-fully-local) example.

## The home folder

All local data lives in `~/.aldwin`. Set `ALDWIN_HOME=/path/to/dir` to use a different folder.

```text
~/.aldwin/                  (mode 0700)
├── config.yaml             model, theme, limits, providers:, run_command.env_allow, connectors.import_paths
├── secrets.yaml            model provider keys (openai, anthropic, gemini, …) and search keys (tavily, brave, exa, …) (mode 0600)
├── *.yaml.lock             empty lock files for config.yaml and secrets.yaml, so two Aldwin windows never write settings at once (mode 0600)
├── persona.md              assistant persona (user-editable or y/n approved)
├── skills/
│   └── using-aldwin/
│       └── SKILL.md        starter skill
├── sessions/
│   └── YYYY-MM-DD_id.json  conversation transcripts saved after every step
├── memory/
│   ├── profile.md          user-written profile (never edited by the agent)
│   ├── notes/
│   │   └── YYYY-MM-DD.md   daily notes written by `remember`
│   └── index.sqlite        rebuildable search index over notes and sessions
├── state/                  migration marker, last-shown tip, recent skills for @, locks/<session id>.lock held while a turn runs, and web.lock (pid, address and start time of a running `aldwin web`)
├── tmp/                    trimmed tool output, cleared after 7 days
├── history                 prompt_toolkit input history
└── logs/
    ├── aldwin.log          rotating event/error log (1 MB × 3, no message content)
    └── approvals.jsonl     one redacted line per policy decision, rotated at 10 MB
```

## The browser window

`aldwin web` listens on a loopback address only (`127.0.0.1`, `localhost` or `::1`), so no other computer can reach it. Each run makes a new random token and prints a link that carries it. Opening the link stores the token in an `aldwin_session` cookie (HttpOnly, SameSite=Strict) and removes it from the address bar.

Anyone holding that link or cookie can use Aldwin as you, with your keys, files and conversations, until the server stops. Do not share the link. Requests from any other page are refused, including pages served by other programs on this computer.

## Secrets pasted into chat

If a message holds something shaped like a token (a GitHub or Slack token, an `sk-` API key for OpenAI, Anthropic, OpenRouter, or DeepSeek, a Google Gemini `AIza…` API key, or a JSON Web Token), Aldwin replaces it with `‹redacted›` before anything leaves your computer. The rest of the message is sent as typed, so "connect me to GitHub, here's my token …" still gets an answer. The model, the saved conversation and its title only ever hold `‹redacted›`, and the line is not written to `history`.

Tokens for custom servers or services without identifiable prefixes cannot be safely detected with regex without false-positive redaction of normal text. For such tokens, enter them through Aldwin's masked prompt (`aldwin setup` or `/model`) rather than pasting into chat.

Lines already in an older `history` file are not rewritten. To give Aldwin a token, use `/connectors`, `/model`, or the private prompt (`Token (hidden):`) it shows when a connector or provider needs one.

## Logs

- `logs/aldwin.log` records lifecycle events and errors only, never message content. Every record passes through a filter that redacts secret-shaped values (`sk-…`, `AIza…`, `tvly-…`). Run `aldwin --debug` for more detail.
- `logs/approvals.jsonl` records every policy decision but never file contents, diffs, or tool outputs. See [Safety](safety.md#approval-log).

## Removing your data

`aldwin uninstall` keeps `~/.aldwin`. `aldwin uninstall --purge` also deletes it, after two confirmations. See [Commands](commands.md#uninstall).
