# Aldwin

An open-source personal assistant that runs on your own computer, in your terminal, built on [Agentling](https://agentling.dev).

![Aldwin 0.0.1 session: install, first run, tool call and command approval](https://raw.githubusercontent.com/lunarstack-co/aldwin/main/docs/assets/demo.gif)

---

## Install

Install with [uv](https://docs.astral.sh/uv/) (Python 3.11+ on macOS or Linux):

```bash
uv tool install aldwin
```

If you don't have `uv` installed yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then start Aldwin:

```bash
aldwin
```

---

## First run

Nothing runs during `uv tool install aldwin`. The first time you run `aldwin`, it sets up your private home folder at `~/.aldwin` (`0700` permissions), shows a short ALDWIN wordmark and a one-screen primer, then asks only for what is missing:

1. **OpenAI API key** (if unset): hidden input, `Checking key… ✓`, saved to `~/.aldwin/secrets.yaml` (`0600`). If `OPENAI_API_KEY` is already set, Aldwin verifies it and still shows the welcome + primer.
2. **Tavily API key (optional):** press `Enter` to skip; add later with `aldwin setup`.
3. Hands off to the live `❯` prompt (default model `gpt-4o`, changeable with `/model`).

Later runs show a compact welcome card and a muted `continuing from …` line (no transcript dump). Use `/history` to review saved messages. Type while Aldwin is working to queue the next message (`Queued: …`); it sends automatically when the turn finishes.

---

## Commands and slash commands

### Terminal commands

| Command | What it does |
|---|---|
| `aldwin` | Open the interactive chat and resume the latest conversation |
| `aldwin setup` | Configure or update API keys and the default model |
| `aldwin uninstall` | Remove the Aldwin command when installed with `uv tool` (confirm, or `--yes`); otherwise print the pipx/pip command. Keeps `~/.aldwin` |
| `aldwin uninstall --purge` | Delete `~/.aldwin` (after two confirmations), then remove or print the uninstall command as above |
| `aldwin --plain` | Disable ANSI colours, live animations, and the terminal bell (also enabled by `NO_COLOR=1`) |
| `aldwin --debug` | Enable `DEBUG`-level event logging in `~/.aldwin/logs/aldwin.log` |
| `aldwin --version` | Print the installed version (`aldwin 0.0.1`) and exit |
| `aldwin --help` | Show CLI flags and subcommands |

### Slash commands (inside `aldwin`)

Type `/` at the prompt for autocomplete:

| Slash command | What it does |
|---|---|
| `/help` | Show slash commands and keyboard shortcuts |
| `/new` | Start a fresh conversation (previous session stays saved on disk) |
| `/history` | Show this conversation’s saved messages (up to 500) |
| `/model [name\|all]` | List available OpenAI chat models and switch models mid-conversation |
| `/persona` | Show the active persona and `~/.aldwin/persona.md` character count |
| `/memory` | Show profile, recent notes, and index status (`N notes · M conversations indexed · index <size>`) |
| `/skills` | List catalogue skills (`name · source · description`); `/skills show <name>` prints the body |
| `@` | Skill picker in the chat input (recent first, Tab to insert); picked skills apply to that message only |
| `/todo` | Show the current multi-step task checklist for this session |
| `/workspace` | Show the workspace folder, or switch it with `/workspace <path>` |
| `/verbose` | Show or hide up to 12 preview lines under file-tool rows for this session |
| `/tools` | List built-in tools and which ones require `y/n` approval |
| `/exit` | Save the current session and exit |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Enter` | Send message (queues while a turn is running) |
| `Alt+Enter` / `Shift+Enter` / `Ctrl+J` | Insert a new line |
| `Esc` | Stop the current answer, close an overlay, or clear a draft (double-Esc) |
| `?` | Shortcuts in the footer (when the prompt is empty) |
| `↑` / `↓` | Navigate input history or move in slash / approval lists |
| `Ctrl+R` | Reverse-search input history |
| `Ctrl+C` | Clear the draft; twice on an empty prompt saves and exits |
| `Ctrl+D` | Save the conversation and exit |

---

## What it can do

### Built-in tools (memory)

| Tool | What it does | Needs `y/n` approval? |
|---|---|---|
| `web_search` | Searches the web via Tavily for current information | No |
| `web_fetch` | Fetches and extracts readable text from an `http`/`https` page (≤ 20,000 chars) | No |
| `run_command` | Runs a shell command in the active workspace | **Always** |
| `remember` | Appends a note, or updates one in place with `replace=<note_id>` | No for new; **Diff** for replace |
| `recall` | Ranked search over notes and saved conversations (`scope`) | No |
| `forget` | Deletes a matching memory note after showing it | **Diff** (never grantable) |

### Skills

Aldwin ships nine default skills (including `using-aldwin`, rendered with the current version and model). User skills live under `~/.aldwin/skills/<name>/SKILL.md` and override a default with the same name. Skills never declare `tools:`; the host strips that key if present. After a turn that writes a skill, the host rebuilds the catalogue before the next turn.

Drop a folder with YAML frontmatter (`name` and `description`) into `~/.aldwin/skills/`:

```markdown
---
name: release-notes
description: Draft concise release notes from git history.
---
Write release notes grouped into Added, Fixed, and Known limitations.
```

List skills with `/skills` or `aldwin skills`; show one body with `/skills show <name>`. Type `@` in chat to pick a skill for the current message (up to three). Ask Aldwin to draft a new skill via `create_skill` (Diff approval, never session-grantable). Document conversion extras: `uv pip install 'aldwin[docs]'` (or `pip install 'aldwin[docs]'`).

### Memory and persona

- **Profile (`~/.aldwin/memory/profile.md`):** durable facts about you (up to 4,000 characters), always included in the system prompt. Only you can edit this file; the agent cannot modify it.
- **Daily notes (`~/.aldwin/memory/notes/YYYY-MM-DD.md`):** short one-line notes saved when Aldwin calls `remember` (up to 500 characters each). Notes from the last 7 days (up to 4,000 characters, newest first) are included in the prompt and age out after a week. A rebuildable SQLite FTS5 index at `~/.aldwin/memory/index.sqlite` powers ranked `recall` over notes and conversations; `/memory` shows how many are indexed.
- **Persona (`~/.aldwin/persona.md`):** controls Aldwin's tone and style (up to 2,000 characters). You can edit the file directly or ask Aldwin to change how it talks (for example, *"Be more direct and concise"*), which shows the proposed `persona.md` in an approval box and asks `y/n` before saving.

---

## Safety model

Web pages, documents, and files can contain prompt-injection text, and `0.0.2` runs on your machine without an OS container sandbox. Aldwin defends against this with a single deterministic policy engine in `aldwin.core.policy` and `aldwin.core.workspace`:

1. **Three approval tiers (`Auto`, `Diff`, `Ask`) in `core/policy.REGISTRY`:**
   - **Auto:** read-only tools inside the active workspace (`read_file`, `read_document`, `list_dir`, `glob`, `grep`), `web_search`, `web_fetch`, `recall`, new `remember` notes, `todo`, and `calculate`.
   - **Diff:** file and state modifications (`edit_file`, `write_file`, `remember` with `replace`, `forget`, `create_skill`, `set_persona`) display a coloured unified diff (folded at 24 lines, `Ctrl+O` to expand) with **No** as the default. Inside-workspace file edits can be granted for that single file for the current session (`a`), except build files (`pyproject.toml`, `Makefile`, `Dockerfile`, `.github/workflows/*.yml`, etc.), which always ask.
   - **Ask:** `run_command` and `download_file` always ask (`y/n`) and can never be session-granted. Reads outside the workspace ask once per folder per session; writes outside the workspace ask every time.
2. **Hard floor (never overridable by any answer or grant):**
   - **Blocked secret paths (`core/policy.BLOCKED`):** `~/.ssh/**`, `~/.aws/**`, `~/.gnupg/**`, `~/.kube/**`, `~/.azure/**`, `~/.config/gh/**`, `~/.config/gcloud/**`, `~/.docker/config.json`, `**/*.pem`, `**/*.key`, `**/*.p12`, `**/*.pfx`, `**/id_rsa*`, `**/id_ed25519*`, `**/id_ecdsa*`, `**/.netrc`, `**/.pgpass`, `**/.npmrc`, `**/.pypirc`, `**/.git-credentials`, `**/.env`, `**/.env.*` (except `.env.example`, `.env.sample`, `.env.template`), `**/.envrc`, and `~/.aldwin/**` (except read-only `~/.aldwin/tmp/**` for `read_file` to page through trimmed tool outputs).
   - **Read-only `.git` floor:** writes to `.git/**` are always refused so hooks and git config cannot be modified by file tools.
   - **Hardline shell refusals (`core/policy.HARDLINE`):** destructive commands (`rm -rf /` or `~` or system folders, `mkfs`, `dd` or redirects to block devices, fork bombs, `kill -9 -1`, `shutdown`/`reboot`/`halt`/`poweroff`/`init 0|6`/`systemctl poweroff`) and commands naming blocked secret paths are refused before asking.
3. **Subprocess & output isolation:**
   - `run_command` starts from an empty environment and copies only safe variables (`PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_*`, `TERM`, `TMPDIR`, `TZ`, `PWD`) plus non-secret names listed in `run_command.env_allow`. Any `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, or `*_CREDENTIAL` variable is stripped.
   - Every tool result (except `todo` and `calculate`) is wrapped in `<untrusted id="u-…" source="…">` tags with inner tags escaped.
   - Declining three actions in a single turn trips a circuit breaker and stops the turn immediately.
4. **Structured approval log (`~/.aldwin/logs/approvals.jsonl`):**
   - Every policy decision (`auto`, `asked`, `blocked`) writes one JSON line (`ts`, `session`, `call_id`, `tool`, `target`, `digest`, `tier`, `decision`, `answer`, `source`, `reason`) with secret-shaped tokens redacted (`‹redacted›`). File contents, diffs, and tool outputs are never logged.
5. **Accepted risks in `0.0.2`:**
   - **Read, then send to the web:** because workspace reads and `web_fetch` are both `Auto`, a prompt-injected document could try to exfiltrate non-blocked workspace text via a fetched URL. Mitigations in `0.0.2`: secret files are blocked by the hard floor, all tool output is wrapped in `<untrusted>`, and every `web_fetch` URL is recorded in `~/.aldwin/logs/approvals.jsonl`.
   - **Best-effort shell inspection:** shell strings cannot be fully parsed statically; the primary control on `run_command` is that every command requires human `y/n` approval and displays yellow risk labels (`⚠ pipes into a shell`, `network download`, `recursive delete`, etc.).
   - **Hard links:** hard links inside the workspace pointing to non-blocked filenames outside the workspace are not detected.

---

## Data and privacy

All local data lives in one directory, `~/.aldwin` (override with `ALDWIN_HOME=/path/to/dir`):

```text
~/.aldwin/                  (mode 0700)
├── config.yaml             model, theme, and character/step limits
├── secrets.yaml            OpenAI and Tavily API keys (mode 0600)
├── persona.md              assistant persona (user-editable or y/n approved)
├── skills/
│   └── using-aldwin/
│       └── SKILL.md        starter skill
├── sessions/
│   └── YYYY-MM-DD_id.json  conversation transcripts saved after every step
├── memory/
│   ├── profile.md          user-written profile (never edited by the agent)
│   └── notes/
│       └── YYYY-MM-DD.md   daily notes written by `remember`
├── history                 prompt_toolkit input history
└── logs/
    └── aldwin.log          rotating event/error log (1 MB × 3, no message content)
```

- **No telemetry:** Aldwin collects no analytics or telemetry.
- **No message content or secrets in logs:** `logs/aldwin.log` records lifecycle events and errors only, and passes all log records through a secret-redacting filter (`sk-…`, `tvly-…`).
- **Network calls:** your prompts and recent conversation turns (last 20 exchanges) are sent directly to OpenAI using your API key. When `web_search` or `web_fetch` runs, search queries go to Tavily and HTTP GET requests go to the target URL.

---

## Limitations in 0.0.1

- **Terminal only, not always-on:** Aldwin runs inside your terminal process. Closing the terminal window stops any running task (the session is saved after every step, and reopening `aldwin` prompts you to say *continue*).
- **OpenAI API key only:** requires an OpenAI API key with access to chat models. Other providers (Anthropic, local models) and browser OAuth sign-in are not in `0.0.1`.
- **Fixed built-in tools:** the five built-in tools cannot be extended with custom Python tools in `0.0.1`.
- **macOS and Linux only:** requires Python `3.11+` on macOS or Linux. Windows is not supported yet.

---

## Documentation

- [Protocol specification (`docs/protocol.md`)](https://github.com/lunarstack-co/aldwin/blob/main/docs/protocol.md)
- [Manual test script & release checklist (`docs/manual-test.md`)](https://github.com/lunarstack-co/aldwin/blob/main/docs/manual-test.md)
- [Release runbook (`docs/releasing.md`)](https://github.com/lunarstack-co/aldwin/blob/main/docs/releasing.md)
- [Changelog (`CHANGELOG.md`)](https://github.com/lunarstack-co/aldwin/blob/main/CHANGELOG.md)

---

## Contributing

Clone the repository and install development dependencies with `uv`:

```bash
git clone https://github.com/lunarstack-co/aldwin.git
cd aldwin
uv sync
```

Run the test suite, linter, formatter check, and type checker before opening a pull request:

```bash
uv run pytest --cov=aldwin
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
```

Found a bug or want to propose an improvement? Open an issue on [GitHub Issues](https://github.com/lunarstack-co/aldwin/issues).

---

## Licence

Apache-2.0. See [`LICENSE`](https://github.com/lunarstack-co/aldwin/blob/main/LICENSE) and [`NOTICE`](https://github.com/lunarstack-co/aldwin/blob/main/NOTICE).
