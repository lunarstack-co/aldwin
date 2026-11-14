# Changelog

All notable changes to Aldwin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Aldwin opens in your browser: `aldwin web` serves the same assistant as the terminal, from your own computer.

### Added

- **`aldwin web`:** a loopback server that opens Aldwin in your browser. Listens on `127.0.0.1` port 7841 (or a free port), prints a one-time link, and takes `--host`, `--port`, `--no-open` and `--workspace`. The UI ships inside the wheel, so no Node is needed to run it.
- **Browser first run:** connect a model with an API key, a local model, or another server, then an optional profile step (first name, occupation, answer styles).
- **Chat in the browser:** streamed answers with highlighted code, tables, Mermaid diagrams and maths; Stop, Retry and Continue; model and search pickers; a context ring; Clean and Detail views; light and dark themes.
- **Conversations:** a sidebar with search, Pinned and Recents, a See all page grouped by date, and rename, pin and delete from each conversation's menu.
- **Approval cards:** Allow, Always allow in this chat (where grantable) and Deny with an optional reason, shown on the step they belong to.
- **Tool displays:** typed displays for terminal output, diffs, file lists, search results, pages, tables, memory and denials.
- **Skills and apps per message:** `@` picks up to three skills and `/` up to three connected apps in the browser's message box.
- **Settings in the browser:** Models, Search, Skills, Tools, Advanced (limits and the update check) and Appearance.
- **Tools page:** connected apps first, then a library by category with one search across both, and a single dialog for connecting, signing in and retrying.
- **Larger connector catalogue:** 34 apps, built from the MCP Registry by `scripts/connectors/import_catalog.py`, with categories.
- **Custom connector sign-in:** a server added by URL that answers 401 gets browser sign-in when it publishes OAuth metadata, and a token prompt otherwise. Its own icon is fetched from its site and kept in `~/.aldwin/state/connector-icons/`.
- **Protocol:** `sessions.list`, `sessions.open`, `sessions.new`, `sessions.rename`, `sessions.pin` and `sessions.delete`; a `pinned` field on conversations; `turn.apps` and `apps.used`; typed `display` on tool results; approval `call_id`; `done.context`; and REST routes under `/v1`. All additive to protocol v2.

### Changed

- **Shared home, two doors:** the terminal and the browser run as separate processes on one home folder. A conversation is locked while a turn runs, so it answers in one place at a time.
- **Settings reload before each turn,** and writes to `config.yaml` and `secrets.yaml` are locked, so a change in one door applies in the other without a restart.
- **Approvals survive a dropped connection:** a waiting approval can be answered after the browser reconnects; one asked while no page is attached is denied.
- **Base instructions:** finish the job, retry or change approach after a failure, respect a declined approval, and look up connector tools first.
- **Docs:** README, contributing, safety, privacy, commands, releasing, manual test and tests docs cover the browser. `SECURITY.md` now says how to report a vulnerability.

### Security

- `aldwin web` refuses any non-loopback host. Every `/v1` route checks the session cookie, `Host`, `Origin` and `Sec-Fetch-Site`.
- The session cookie is HttpOnly and SameSite=Strict, and the token is removed from the address bar after sign-in.
- The WebSocket needs a single-use ticket that lasts 30 seconds, and only one browser tab is connected at a time.
- API keys saved from the browser are checked with the provider first and only ever returned masked.
- Connector icons are fetched without credentials, from the app's own host or parent domain only, and served with a sandboxing CSP.

### Upgrade notes

- Upgrading from `0.0.5` keeps sessions, notes, memory index, approvals, connectors, persona, keys, and settings. No home migration runs.
- Run `uv tool upgrade aldwin`, then `aldwin web`.

### Known limitations

- The browser interface only opens on the computer running `aldwin web`.
- No attachments, dictation, or editing of sent messages in the browser.
- The workspace cannot be changed from the browser; restart with `--workspace`.
- Tool displays are live only: after a reload, past tool rows are plain.
- Windows is not supported. Local MCP servers remain unsupported on Windows.

## [0.0.5] - 2026-11-07

Aldwin can search the web your way — self-hosted SearXNG or a hosted key — all behind the same `web_search` tool.

### Added

- **Multi-provider web search:** connect SearXNG or a hosted provider (Tavily, Brave, Exa, Perplexity, Parallel, Firecrawl) with `/search`. Pick a provider, paste a key (checked before save), switch, update, or disconnect. Status with `/search status`.
- **Shared search registry:** `search.provider: auto` (default) picks the first connected backend in catalogue order; set a specific name to pin one. Env vars win over secrets (`TAVILY_API_KEY`, `BRAVE_API_KEY` / `BRAVE_SEARCH_API_KEY`, `EXA_API_KEY`, `PERPLEXITY_API_KEY`, `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`, `SEARXNG_URL`).
- **SearXNG:** local auto-detect on `:8080`, or enter a URL; JSON output must be enabled on the instance. See [Set up SearXNG](docs/searxng.md).
- **Hosted key checks:** Aldwin validates each key with the provider before writing `~/.aldwin/secrets.yaml` (0600). Brave key check uses one query; Tavily check can take up to 20 seconds.
- **`/copy`:** copy Aldwin's last answer to the clipboard as markdown (`/copy`), or only its last fenced code block (`/copy code`). Uses OSC 52 when available (including over SSH), then `pbcopy` / `wl-copy` / `xclip` (#72).
- **Tip:** welcome card rotates "Connect web search with /search — SearXNG or a hosted key."

### Changed

- **What's-new:** upgrade panel explains multi-provider web search; existing Tavily-only setups keep working via auto.
- **README and docs:** web search backends, privacy (what leaves the machine), Brave quota note, SearXNG JSON requirement, `/search` and `/copy` commands, manual-test Step 21.

### Security

- Search keys follow env-wins and check-then-save; pasted keys are never logged.
- Hosted search sends the query to the chosen provider under that provider's terms; prefer self-hosted SearXNG when you want queries to stay local.
- No keys in docs examples.

### Upgrade notes

- Upgrading from `0.0.4` keeps sessions, notes, memory index, approvals, connectors, persona, model keys, and settings. No home migration; an existing Tavily key continues to work with `search.provider: auto`.
- Run `uv tool upgrade aldwin`. Connect more backends any time with `/search`.

### Known limitations

- DuckDuckGo is not in the catalogue for this release.
- Firecrawl scrape (beyond search) and browser connectors are out of scope.
- Windows is not supported. Local MCP servers remain unsupported on Windows.

### Sizes

- Wheel and installed size: record after TestPyPI / CI (budget ≤120 MB installed, no extras).

## [0.0.4] - 2026-11-05

Aldwin can use any first-ship model provider, including local servers — no OpenAI lock-in.

### Added

- **Any model, including local:** switch between cloud and local providers with `/model`. Built-in catalogue (bundled `providers.yaml`, no online lookup): Ollama, LM Studio, OpenAI, Anthropic, Google Gemini, OpenRouter, and DeepSeek.
- **Local-first first run:** on launch Aldwin probes Ollama and LM Studio. Path A uses a found local model with no account; Path B offers cloud or install steps; Path C keeps an existing OpenAI setup with no reconfigure.
- **Three HTTP translators** (no vendor SDKs): OpenAI-compatible chat, Anthropic Messages, and Ollama native — plain `httpx` only.
- **Key store:** environment variables win and are never written to disk; pasted cloud keys are checked against the provider before save; secrets stay in `~/.aldwin/secrets.yaml` (0600).
- **Friendly provider errors:** Agentling error kinds map to copy that names the provider and what to do next, with branded retry status lines.
- **Protocol doors:** `models.list`, `provider.key`, and `provider.server` for listing models, saving keys, and registering Other server entries under `providers:` in `config.yaml`.
- **Other server:** add any OpenAI-compatible (or Anthropic/Ollama-shaped) endpoint via `/model` or `providers:` (`shape`, `local`, `base_url`, `secret`, `title`).

### Changed

- **Agentling 0.2:** depends on `agentling>=0.2,<0.3` from PyPI; the OpenAI Python SDK is not a dependency.
- **What's-new and tip:** upgrade panel explains any model including local; tip says "Switch between cloud and local models any time with /model."
- **README and docs:** "Run it fully local" quickstart, first-ship provider table, Fully local privacy, provider-neutral protocol error wording.

### Removed

- OpenAI-only product wording from the README and release messaging (OpenAI remains a first-ship cloud option).

### Security

- Provider keys follow env-wins and check-then-save; pasted keys are validated before persist.
- Local model traffic on loopback does not leave the machine (except optional web tools, remote connectors, and the PyPI update check).
- No `openai` / `anthropic` vendor SDK packages in the install (size and supply-chain).

### Upgrade notes

- Upgrading from `0.0.3` keeps sessions, notes, memory index, approvals, connectors, persona, and settings. No home migration runs; bare model ids still resolve as `openai/<id>`.
- Run `uv tool upgrade aldwin`. OpenAI users keep working (Path C).

### Known limitations

- First-ship providers only in the built-in catalogue; other backends use Other server / `providers:`.
- Windows is not supported. Local MCP servers remain unsupported on Windows.

### Sizes

- Wheel and installed size: record after TestPyPI / CI (budget ≤120 MB installed, no extras).

## [0.0.3] - 2026-11-01

Aldwin can now connect to your other apps through MCP servers, and warns you when it detects a secret in your message.

### Added

- **Connectors:** connect Aldwin to Notion, Linear, GitHub, Context7, and other apps with `/connectors`. Each connector is an MCP server that adds tools to the current session.
  - **Catalogue:** pick a service from the built-in list and sign in through your browser; Aldwin handles the OAuth flow and stores the token at `~/.aldwin/credentials/<name>.json` (mode 0600).
  - **Custom URL:** paste any remote MCP server URL that is not in the catalogue.
  - **Local servers:** run any MCP server from a shell command (for example `npx -y @modelcontextprotocol/server-filesystem /path`); the command is shown for approval before it starts, and re-approved after any hand edit. Local servers are not supported on Windows.
  - **Import:** `aldwin connectors import` reads connector configs from other agent apps on your machine and adds compatible entries.
  - **Ask Aldwin to connect:** type "connect me to Notion" and Aldwin proposes the right connector for you to approve.
  - **On-demand tool loading:** tools are loaded from the server only when the model needs them, so the context window is not pre-filled with hundreds of tool definitions. Up to 40 connector tools can be active at once; `/new` clears the loaded set.
  - **Trust toggle:** in `/connectors`, toggle trust on a server to control whether its read-only tools run on Auto or always ask first.
- **Pasted-secret warning:** Aldwin detects patterns that look like API keys, tokens, or passwords in your message before it is sent to the model. It shows the detected pattern and asks you to confirm or cancel. If you confirm, the text is redacted from the session file and from `logs/approvals.jsonl` so it is not stored at rest.
- **`mcp` version in `--version`:** `aldwin --version` now reports the installed `mcp` SDK version alongside `agentling`, for example `aldwin 0.0.3 (protocol v2, agentling 0.1.1, mcp 2.3.0)`.
- **Connectors tip:** the rotating welcome-card tip now includes "Try: /connectors to connect Notion, Linear or GitHub" (15 tips total).

### Fixed

- The `set_persona` tool always told the model the persona limit was 2,000 characters, even when `limits.persona_chars` was set to something else. With a lower limit the model wrote text that was then rejected. The tool now states the configured limit.
- `/memory` always said "Notes in the last 7 days" and counted over a fixed seven days, while the prompt uses `limits.notes_days`. The count and label now follow `notes_days`.
- `/tools` labelled every tool that needs approval "(always asks y/n)". That was wrong for `edit_file` and `write_file`, which can be allowed per file for the session with `a`, and for the diff-approved tools, which don't ask y/n. Each tool now shows a note that matches its approval tier. `run_command` is described as running in the workspace, not the launch folder.
- Setting `theme: light` or `theme: colour_blind` in `~/.aldwin/config.yaml` had no effect: chat and `aldwin setup` always used the dark theme. The configured theme is now applied. `theme: plain` already worked and is unchanged.
- The "Stopped after N steps" notice always said 15, even when `limits.max_steps` in `~/.aldwin/config.yaml` was set to something else. It now shows the configured limit. `DoneEvent` carries the limit in a new optional `max_steps` field when `reason` is `max_steps`.
- Some invalid `config.yaml` values produced garbled errors such as `'check_for_updates' must be Input should be a valid boolean…`. `check_for_updates` and `last_seen_version` now have proper messages, and a bad item in `run_command.env_allow` is named by its position (`run_command.env_allow[0]`).
- Running `aldwin` (or `aldwin skills`) from a folder that had been deleted after you `cd`-ed into it crashed with a `FileNotFoundError` traceback. Aldwin now falls back to `~/aldwin_workspace`, the same as when it is launched from the home directory.

### Security

- Aldwin warns and asks for confirmation when it detects a secret-looking pattern in your message before sending it to the model. Confirmed messages have the detected text redacted in the session file and in `logs/approvals.jsonl`.
- Connector sign-in tokens are stored at `~/.aldwin/credentials/<name>.json` (mode 0600) and never passed through the model or written to logs.
- Local MCP server commands are shown for approval before they start and are re-approved after any hand edit. They run with a minimal environment (`clean_env`).
- Connector tool output is wrapped in `<untrusted>` the same as file and command output, and is never trusted as instructions.
- The `~/.aldwin/credentials/` path is blocked from every tool.

### Upgrade notes

- Upgrading from `0.0.2` or `0.0.2.1` keeps all your sessions, notes, memory index, persona, and settings. No migration runs on first launch; just `uv tool upgrade aldwin`.
- There is no `connectors.yaml` or `credentials/` folder until you add a connector with `/connectors`.
- The `mcp` package (~18-27 MB) is now a production dependency. Total installed size is higher than `0.0.2`; see Sizes below.

### Known limitations

- Local MCP servers run with your user's rights; there is no sandbox. A server can mark a write tool as `readOnlyHint: true`, and Aldwin will trust that hint.
- Connector tools are loaded on demand and counted against `limits.connector_tools` (default 40). `/new` clears the loaded set if you need a fresh slate.
- Only macOS 13+ and Linux are supported. Local MCP servers are not supported on Windows.

### Sizes

- Wheel: ~0.34 MB. Installed (no extras): to be confirmed in CI (Linux x86_64, Python 3.11–3.13). macOS arm64: see CI run.

## [0.0.2.1] - 2026-10-25

### Fixed

- Running `aldwin` from the home directory, or from any folder that cannot be a workspace (`/`, or inside `~/.aldwin`), crashed with a traceback. Aldwin now creates and uses `~/aldwin_workspace` for that session, shown in the welcome panel as `workspace: ~/aldwin_workspace`. Pass `--workspace PATH` or start Aldwin inside a project folder to choose a different root.

### Changed

- The one-time What's new panel is keyed to the feature release (`major.minor.micro`), so a patch release such as this one does not show the `0.0.2` panel a second time.

### Security

- Upgraded the pinned `certifi` to `2026.10.23`. Earlier releases bundled emSign root CAs whose intermediate CA keys were compromised (GHSA-6q48-6c7w-9gw2); `web_fetch` and `download_file` verify TLS against this bundle.

## [0.0.2] - 2026-10-30

Aldwin now works with your files and documents, knows good habits out of the box, and lets you pick a skill with `@`.

### Added

- **File tools with diff approval:** `list_dir`, `glob`, `grep`, `read_file` (read-only); `edit_file` and `write_file`, each shown as a folded diff before anything is written to disk.
- **Document reading:** `read_document` converts PDF (page-range selection with `pages=`), Word, PowerPoint, Excel (`sheet=` by name or index), CSV/TSV, and HTML to markdown. `download_file` saves a public URL into the workspace after approval.
- **Planning tools:** `todo` keeps a checklist for jobs of three or more steps, shown as a live view in the CLI; `calculate` does exact arithmetic and ISO date maths without a shell.
- **Upgraded memory:** `recall` now does ranked search across notes and past conversations; notes can be corrected in place (`replace=`) or removed (`forget`, after diff approval) instead of only growing.
- **Nine packaged default skills** (`using-aldwin`, `files`, `markdown-notes`, `documents`, `making-documents`, `web-research`, `memory`, `terminal`, `skill-creator`) loaded from the install via `importlib.resources`; user skills under `~/.aldwin/skills/<name>/` override by name.
- **`@` skill picker:** type `@` to insert a skill into the turn; `/skills` and `aldwin skills` list `name · source · description` with `default` / `user` / `user override` labels, and `/skills show <name>` prints a skill's effective body.
- **`create_skill` tool** (diff approval, never session-grantable) to write or update a user skill; the host rebuilds the agent after the turn and shows `skills reloaded`.
- **`aldwin[docs]` optional extra** (`python-docx`, `openpyxl`, `reportlab`) for the `making-documents` skill to write Office files via `run_command`.
- **`aldwin[office]` optional extra** (`mammoth`, `python-pptx`, `openpyxl`) for reading `.docx`, `.pptx`, and `.xlsx`. It is left out of the default install to keep the installed size within budget; without it, `read_document` says how to enable those formats. PDF, CSV/TSV, and HTML always work.
- **Protocol v2** (`hello(v_min, v_max)` negotiation): a v2 door gets Diff-tier approvals with digests; a v1 door still completes a turn with plain y/n approvals, and any Diff-tier call from a v1 door is refused as unapprovable rather than silently downgraded.
- **New slash commands:** `/todo` (checklist view), `/skills` (catalogue and `show`), `/verbose` (toggle file-tool previews), `/workspace` (inspect or change the workspace root).
- **One-time What's new panel** on the first everyday launch after an upgrade, and three new idle tips pointing at the file, document, and skill-picker workflows.
- **PyPI update-available nudge:** a muted one-line notice under the welcome card when a newer `aldwin` is published, opt out with `check_for_updates: false`.

### Changed

- **Policy engine:** every tool call now goes through a deterministic three-tier approval pipeline (session-grantable, per-call y/n, and Diff approval that can never be session-granted) with a hard floor that denies outright before any tier is even offered.
- **`run_command` runs with a clean, minimal environment.** Secrets and API keys are no longer visible to spawned commands by default; extra variable names to pass through are opt-in via `run_command.env_allow` in `config.yaml` (secret-looking names are always ignored).
- **`run_command` always asks, with No as the default answer**, and refuses outright (no prompt) a short list of privilege-escalation, interactive-program, and bare-REPL commands, and anything naming a blocked path (see Security).
- Default skills are no longer copied into `~/.aldwin` on first run; upgrades improve packaged skills automatically.
- Skills never load code: a `tools:` frontmatter key is stripped (flagged as `tools removed` for user skills).

### Security

- **Hard floor, fail closed:** a fixed set of paths that commonly hold secrets (`.ssh/`, `.aws/`, `.gnupg/`, `.kube/`, `.docker/config.json`, `*.pem`/`*.key`/`*.p12`/`*.pfx`, SSH private keys, `.netrc`, `.pgpass`, `.npmrc`, `.pypirc`, `.git-credentials`, `.env*`, and `~/.aldwin/` itself) is never readable, writable, or passed to `run_command`, with no config override (except read-only `~/.aldwin/tmp/**` for `read_file`). This check runs before any approval tier and cannot be session-granted away.
- **Approvals bound to what was shown:** a Diff-tier approval carries a `sha256` digest over the tool name, resolved arguments, and the diff text; if the file or digest changed since the diff was shown, Aldwin re-confirms once rather than applying a stale approval.
- **Every tool result is wrapped as `<untrusted>`** before it reaches the model, so text read from a file, page, or command output is never treated as an instruction.
- **Approvals log:** every policy decision (approved, denied, blocked, refused) is appended as one redacted JSON line to `~/.aldwin/logs/approvals.jsonl`, rotated at 10 MB.
- **Clean command environment:** see `run_command` above.

### Upgrade notes

- Upgrading from `0.0.1` runs an automatic, idempotent migration on first launch: `config.yaml` is backed up to `config.yaml.bak-0.0.1` (once), gains `run_command.env_allow: []` with every existing value and comment preserved, `tmp/`, `state/`, and `logs/` are created or repaired to `0700`, and `memory/index.sqlite` is built from existing notes and sessions. Nothing in `~/.aldwin` is deleted. A second launch is a no-op.
- A command that used to see `$OPENAI_API_KEY` (or any other secret-looking variable) under `0.0.1` no longer does; add its name to `run_command.env_allow` if a command genuinely needs a non-secret variable passed through.
- An unmodified `0.0.1` starter copy of `~/.aldwin/skills/using-aldwin/` is renamed to `using-aldwin.old-0.0.1/` so the new packaged identity block takes effect; an edited copy is kept as a user override.
- The one-time What's new panel shows once on the first launch after upgrading, then never again for that version.

### Known issues

- A workspace file's contents could, in principle, be read by Aldwin and included in a URL it is asked to fetch. Review `run_command` and `web_fetch` approvals for anything that forwards file contents externally.
- A hard link inside the workspace pointing to a file outside it can resolve as if it were inside, letting a file read skip the outside-workspace approval step; its own filename is still checked against the blocked-path list.
- `run_command`'s refusal checks parse the command as text, not a real shell grammar; unusual quoting or encoding could in principle evade a check meant to catch it.

### Sizes

- Wheel: ~0.23 MB. Installed (no extras): ~116 MB on macOS arm64; confirmed within the 120 MB budget on Linux x86_64 (Python 3.11, 3.12, and 3.13) in CI. With `aldwin[office]`: ~131 MB on macOS arm64.

## [0.0.1] - 2026-11-01

First public release of Aldwin: an open-source personal assistant that runs on your own computer, in your terminal, built on [Agentling](https://agentling.dev).

### Added

- **One-command install:** `uv tool install aldwin` installs the `aldwin` CLI (`aldwin`, `aldwin setup`, `aldwin uninstall [--purge]`, `--version`, `--plain`, `--debug`).
- **Private local home folder (`~/.aldwin`):** created on first run with mode `0700` (overridable via `ALDWIN_HOME`), atomic file writes (`fsync` + `os.replace`), rotating file logs (`logs/aldwin.log`, `1 MB × 3`) with secret redaction and no message content logged, and separate `secrets.yaml` (`0600`) with environment-variable priority (`OPENAI_API_KEY`, `TAVILY_API_KEY`).
- **First-run onboarding & setup:** guided terminal setup that prompts for an OpenAI API key with hidden input, verifies it against OpenAI, optionally configures a Tavily web search key, and saves a default chat model.
- **One brain, many doors architecture:** typed Pydantic v2 protocol (`aldwin.protocol`), in-process `asyncio.Queue` host (`aldwin.host`), client SDK (`aldwin.client`), and CLI door (`aldwin.cli`) with strict architectural isolation (`cli` never imports `core` or `agentling`).
- **Agentling + OpenAI core:** streaming responses, automatic exponential-backoff retries on transient OpenAI errors, dynamic context manager keeping the last 20 exchanges on clean turn boundaries, and user-extensible Markdown skills in `~/.aldwin/skills/<name>/SKILL.md` (shipping with the `using-aldwin` starter skill).
- **Five built-in tools:**
  - `web_search`: web search via Tavily behind a swappable `SearchProvider` interface.
  - `web_fetch`: HTTP/HTTPS page extraction via `httpx` + `trafilatura` (capped at 20,000 chars, wrapped as `<untrusted>` data, with DNS/IP SSRF blocking against localhost and private networks).
  - `run_command`: shell command execution in the launch directory with mandatory `y/n` user approval, pre-execution refusal of `sudo` and interactive programs, secret environment stripping (`*_KEY`, `*_TOKEN`, `*_SECRET`), 60-second timeout, and 10,000-char output cap.
  - `remember`: appends a single-line dated note with metadata to `~/.aldwin/memory/notes/YYYY-MM-DD.md` (last 7 days injected into context, up to 4,000 chars).
  - `recall`: keyword search across daily notes.
- **User-controlled memory and persona:** `~/.aldwin/memory/profile.md` (user-editable only, up to 4,000 chars) and `~/.aldwin/persona.md` (up to 2,000 chars, updated via `set_persona` only after explicit `y/n` user approval).
- **Session persistence & recovery:** conversations saved atomically to `~/.aldwin/sessions/<YYYY-MM-DD>_<id>.json` after every step; reopening `aldwin` displays the last 20 messages and resumes where you left off, with an interrupted-turn notice if the previous terminal closed mid-task.
- **Inline terminal UI:** built with Typer, Rich, and `prompt_toolkit` in native terminal scrollback, featuring fence-aware streaming Markdown, live spinner and status bar, tool cards, bordered approval prompts with terminal bell, `Esc` mid-turn stop within 1 second, `?` keyboard shortcut guide, plain mode (`--plain` / `NO_COLOR`), and slash commands (`/help`, `/new`, `/model`, `/persona`, `/memory`, `/skills`, `/tools`, `/exit`).

### Known limitations

- **Terminal only, not always-on yet:** Aldwin `0.0.1` runs inside your terminal process. Closing the terminal stops the active task (your conversation is saved after every step and resumes next time you run `aldwin`). Background daemon mode over `~/.aldwin/aldwin.sock` arrives in `0.1`.
- **OpenAI API key only:** requires your own OpenAI API key (`OPENAI_API_KEY` or configured on first run). Other model providers and account OAuth are not included in `0.0.1`.
- **Fixed built-in toolset:** includes the five built-in tools (`web_search`, `web_fetch`, `run_command`, `remember`, `recall`); custom code tools cannot be added in `0.0.1` (drop-in Markdown skills in `~/.aldwin/skills/` are supported).
- **macOS and Linux only:** requires Python `3.11+` on macOS or Linux. Windows is not supported yet.

[0.0.5]: https://github.com/lunarstack-co/aldwin/releases/tag/v0.0.5
[0.0.4]: https://github.com/lunarstack-co/aldwin/releases/tag/v0.0.4
[0.0.3]: https://github.com/lunarstack-co/aldwin/releases/tag/v0.0.3
[0.0.2.1]: https://github.com/lunarstack-co/aldwin/releases/tag/v0.0.2.1
[0.0.2]: https://github.com/lunarstack-co/aldwin/releases/tag/v0.0.2
[0.0.1]: https://github.com/lunarstack-co/aldwin/releases/tag/v0.0.1
