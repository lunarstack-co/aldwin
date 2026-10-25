# Aldwin

An open-source personal assistant that works with your files and documents, knows good habits out of the box, and runs on your own computer, in your terminal, built on [Agentling](https://agentling.dev).

![Aldwin 0.0.2 session: install, first run, tool call and command approval](https://raw.githubusercontent.com/lunarstack-co/aldwin/main/docs/assets/demo.gif)

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

Or install with `pip`:

```bash
pip install aldwin
```

Then start Aldwin:

```bash
aldwin
```

Already on `0.0.1`? Upgrade in place and keep your conversation, memory, persona, and settings:

```bash
uv tool upgrade aldwin
```

### First run

Nothing runs during install. The first time you run `aldwin`, it sets up your private home folder at `~/.aldwin` (`0700` permissions), shows a short primer, then asks only for what is missing:

1. **OpenAI API key** (if unset): hidden input, `Checking key… ✓`, saved to `~/.aldwin/secrets.yaml` (`0600`). If `OPENAI_API_KEY` is already set, Aldwin verifies it and still shows the welcome + primer.
2. **Tavily API key (optional):** press `Enter` to skip; add later with `aldwin setup`.
3. Hands off to the live `❯` prompt (default model `gpt-4o`, changeable with `/model`), with tips pointing at files, documents, and the `@` skill picker.

Later runs show a compact welcome card and a muted `continuing from …` line (no transcript dump); upgrading from `0.0.1` additionally shows a one-time What's new panel. Use `/history` to review saved messages. Type while Aldwin is working to queue the next message (`Queued: …`); it sends automatically when the turn finishes.

---

## What Aldwin can do

### Files

Read-only tools run without asking; edits show a diff first.

| Tool | What it does | Needs approval? |
|---|---|---|
| `list_dir` | Lists files and folders in the workspace | No |
| `glob` | Finds files by name with a glob pattern | No |
| `grep` | Searches file contents for a regular expression | No |
| `read_file` | Reads a text file and returns numbered lines | No |
| `edit_file` | Replaces a unique exact string in a file | **Diff** |
| `write_file` | Creates or overwrites a text file | **Diff** |

### Documents

| Tool | What it does | Needs approval? |
|---|---|---|
| `read_document` | Converts a PDF, Word, PowerPoint, Excel, CSV/TSV, or HTML file to markdown (`pages=` for PDF ranges, `sheet=` for an Excel sheet) | No |
| `download_file` | Downloads a public URL into the workspace | **Ask** |

Reading `.docx`, `.pptx`, and `.xlsx` needs the `aldwin[office]` extra (`uv tool install "aldwin[office]"` or `pip install "aldwin[office]"`); `read_document` says so if it's missing. PDF, CSV/TSV, and HTML always work.

### Notes

| Tool | What it does | Needs approval? |
|---|---|---|
| `remember` | Saves a short durable fact, or updates one in place with `replace=` | No for new; **Diff** for replace |
| `recall` | Ranked search across daily notes and past conversations | No |
| `forget` | Deletes a saved note after showing it | **Diff** (never grantable) |

Notes live in `~/.aldwin/memory/notes/YYYY-MM-DD.md`; a rebuildable SQLite FTS5 index at `~/.aldwin/memory/index.sqlite` powers ranked `recall`.

### Memory and persona

- **Profile (`~/.aldwin/memory/profile.md`):** durable facts about you (up to 4,000 characters), always included in the system prompt. Only you can edit this file; the agent cannot modify it.
- **Persona (`~/.aldwin/persona.md`):** controls Aldwin's tone and style (up to 2,000 characters). Edit the file directly, or ask Aldwin to change how it talks; it shows the proposed `persona.md` in an approval box and asks `y/n` before saving.

### Web

| Tool | What it does | Needs approval? |
|---|---|---|
| `web_search` | Searches the web via Tavily for current information | No |
| `web_fetch` | Fetches and extracts readable text from an `http`/`https` page (≤ 20,000 chars) | No |

### Terminal

| Tool | What it does | Needs approval? |
|---|---|---|
| `run_command` | Runs a shell command in the workspace, with a clean environment | **Always** |

### Planning

| Tool | What it does | Needs approval? |
|---|---|---|
| `todo` | Keeps a checklist for jobs of three or more steps, shown as a live view | No |
| `calculate` | Exact arithmetic and ISO date maths, no shell | No |

### Skills and the `@` picker

Type `@` to pick a skill, or let Aldwin pick one on its own. See [Skills](#skills) below for the full default set and how to write your own.

---

## Safety model

Web pages, documents, and files can contain prompt-injection text, and `0.0.2` runs on your machine without an OS container sandbox. Aldwin defends against this with a single deterministic policy engine in `aldwin.core.policy` and `aldwin.core.workspace`:

1. **Three approval tiers (`Auto`, `Diff`, `Ask`) in `core/policy.REGISTRY`:**
   - **Auto:** read-only tools inside the active workspace (`read_file`, `read_document`, `list_dir`, `glob`, `grep`), `web_search`, `web_fetch`, `recall`, new `remember` notes, `todo`, and `calculate`.
   - **Diff:** file and state modifications (`edit_file`, `write_file`, `remember` with `replace`, `forget`, `create_skill`, `set_persona`) display a coloured unified diff (folded at 24 lines, `Ctrl+O` to expand) with **No** as the default. Inside-workspace file edits can be granted for that single file for the current session (`a`), except build files (`pyproject.toml`, `Makefile`, `Dockerfile`, `.github/workflows/*.yml`, etc.), which always ask.
   - **Ask:** `run_command` and `download_file` always ask (`y/n`) and can never be session-granted. Reads outside the workspace ask once per folder per session; writes outside the workspace ask every time.
2. **Hard floor (never overridable by any answer, grant, or config key):**
   - **Blocked secret paths (`core/policy.BLOCKED`):** `~/.ssh/**`, `~/.aws/**`, `~/.gnupg/**`, `~/.kube/**`, `~/.azure/**`, `~/.config/gh/**`, `~/.config/gcloud/**`, `~/.docker/config.json`, `**/*.pem`, `**/*.key`, `**/*.p12`, `**/*.pfx`, `**/id_rsa*`, `**/id_ed25519*`, `**/id_ecdsa*`, `**/.netrc`, `**/.pgpass`, `**/.npmrc`, `**/.pypirc`, `**/.git-credentials`, `**/.env`, `**/.env.*` (except `.env.example`, `.env.sample`, `.env.template`), `**/.envrc`, and `~/.aldwin/**` (except read-only `~/.aldwin/tmp/**` for `read_file` to page through trimmed tool outputs).
   - **Read-only `.git` floor:** writes to `.git/**` are always refused so hooks and git config cannot be modified by file tools.
   - **Always-refused commands:** privilege escalation (`sudo`, `su`, `doas`), interactive programs and bare REPLs, destructive commands (`rm -rf /` or `~` or system folders, `mkfs`, `dd` or redirects to block devices, fork bombs, `kill -9 -1`, `shutdown`/`reboot`/`halt`/`poweroff`/`init 0|6`/`systemctl poweroff`), and commands naming a blocked secret path, are refused before asking.
3. **Clean command environment:** `run_command` starts from an empty environment and copies only safe variables (`PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_*`, `TERM`, `TMPDIR`, `TZ`, `PWD`) plus non-secret names you list in `run_command.env_allow` in `config.yaml`. Any `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, or `*_CREDENTIAL` variable is stripped, even if named in `env_allow`.
4. **Approvals bound to what was shown, every result untrusted:** a Diff-tier approval carries a `sha256` digest over the tool, resolved arguments, and the diff; if the file or digest changed since the diff was shown, Aldwin re-confirms once rather than applying a stale approval. Every tool result (except `todo` and `calculate`) is wrapped in `<untrusted id="u-…" source="…">` tags with inner tags escaped, so text read from a file, page, or command output is never treated as an instruction. Declining three actions in a single turn trips a circuit breaker and stops the turn immediately.
5. **Structured approval log (`~/.aldwin/logs/approvals.jsonl`):** every policy decision (`auto`, `asked`, `blocked`) writes one JSON line (`ts`, `session`, `call_id`, `tool`, `target`, `digest`, `tier`, `decision`, `answer`, `source`, `reason`), rotated at 10 MB, with secret-shaped tokens redacted (`‹redacted›`). File contents, diffs, and tool outputs are never logged.
6. **Known limits in `0.0.2`:**
   - **Read, then send to the web:** because workspace reads and `web_fetch` are both `Auto`, a prompt-injected document could try to exfiltrate non-blocked workspace text via a fetched URL. Mitigations: secret files are blocked by the hard floor, all tool output is wrapped in `<untrusted>`, and every `web_fetch` URL is recorded in the approvals log.
   - **Hard links:** a hard link inside the workspace pointing to a non-blocked filename outside the workspace is not detected.
   - **Best-effort shell inspection:** shell strings cannot be fully parsed statically; the primary control on `run_command` is that every command requires human `y/n` approval and displays yellow risk labels (`⚠ pipes into a shell`, `network download`, `recursive delete`, etc.).

---

## Skills

Aldwin ships nine default skills (`using-aldwin`, `files`, `markdown-notes`, `documents`, `making-documents`, `web-research`, `memory`, `terminal`, `skill-creator`), loaded from the install. User skills under `~/.aldwin/skills/<name>/SKILL.md` override a default with the same name. Skills never declare `tools:`; the host strips that key if present. After a turn that writes a skill, the host rebuilds the catalogue before the next turn.

Drop a folder with YAML frontmatter (`name` and `description`) into `~/.aldwin/skills/`:

```markdown
---
name: release-notes
description: Draft concise release notes from git history.
---
Write release notes grouped into Added, Fixed, and Known limitations.
```

List skills with `/skills` or `aldwin skills`; show one body with `/skills show <name>`. Type `@` in chat to pick a skill for the current message (up to three). Ask Aldwin to draft a new skill via `create_skill` (Diff approval, never session-grantable). The `making-documents` skill writes Office files via `run_command`; enable it with the document-writing extra: `uv tool install "aldwin[docs]"` (or `pip install "aldwin[docs]"`).

---

## Data and privacy

All local data lives in one directory, `~/.aldwin` (override with `ALDWIN_HOME=/path/to/dir`):

```text
~/.aldwin/                  (mode 0700)
├── config.yaml             model, theme, limits, and run_command.env_allow
├── secrets.yaml            OpenAI and Tavily API keys (mode 0600)
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
├── state/                  migration marker, last-shown tip, recent skills for @
├── tmp/                    trimmed tool output, cleared after 7 days
├── history                 prompt_toolkit input history
└── logs/
    ├── aldwin.log          rotating event/error log (1 MB × 3, no message content)
    └── approvals.jsonl     one redacted line per policy decision, rotated at 10 MB
```

- **No telemetry:** Aldwin collects no analytics or telemetry.
- **What is sent to OpenAI:** your prompts, the last 20 conversation exchanges, your profile and recent daily notes (within their character caps), and the contents of any file, document, or page Aldwin reads for the turn.
- **What never leaves the machine:** everything else under `~/.aldwin`. Secrets, the approvals log, the memory index, and session transcripts are read locally only.
- **Other network calls:** when `web_search` or `web_fetch` runs, search queries go to Tavily and HTTP GET requests go to the target URL. On everyday launch, Aldwin may GET public project metadata from `https://pypi.org/pypi/aldwin/json` to show an update-available line (no user id, keys, or home path sent); set `check_for_updates: false` in `config.yaml` to opt out.
- **No message content or secrets in logs:** `logs/aldwin.log` records lifecycle events and errors only, and passes all log records through a secret-redacting filter (`sk-…`, `tvly-…`); `logs/approvals.jsonl` never logs file contents, diffs, or tool outputs.

---

## Commands and slash commands

### Terminal commands

| Command | What it does |
|---|---|
| `aldwin` | Open the interactive chat and resume the latest conversation |
| `aldwin setup` | Configure or update API keys and the default model |
| `aldwin skills` | List default and user skills from the terminal (same catalogue as `/skills`) |
| `aldwin uninstall` | See [Uninstall](#uninstall) |
| `aldwin --plain` | Disable ANSI colours, live animations, and the terminal bell (also enabled by `NO_COLOR=1`) |
| `aldwin --debug` | Enable `DEBUG`-level event logging in `~/.aldwin/logs/aldwin.log` |
| `aldwin --version` | Print the installed version and protocol/Agentling versions, then exit |
| `aldwin --help` | Show CLI flags and subcommands |

### Slash commands (inside `aldwin`)

Type `/` at the prompt for autocomplete:

| Slash command | What it does |
|---|---|
| `/help` | Show slash commands and keyboard shortcuts |
| `/new` | Start a fresh conversation (previous session stays saved on disk) |
| `/history` | Show this conversation's saved messages (up to 500) |
| `/model [name\|all]` | List available OpenAI chat models and switch models mid-conversation |
| `/persona` | Show the active persona and `~/.aldwin/persona.md` character count |
| `/memory` | Show profile, recent notes, and index status (`N notes · M conversations indexed · index <size>`) |
| `/skills` | List catalogue skills (`name · source · description`); `/skills show <name>` prints the body |
| `@` | Skill picker in the chat input (recent first, Tab to insert); picked skills apply to that message only |
| `/todo` | Show the current multi-step task checklist for this session |
| `/workspace` | Show the workspace folder, or switch it with `/workspace <path>` |
| `/verbose` | Show or hide up to 12 preview lines under file-tool rows for this session |
| `/tools` | List built-in tools and which ones require approval |
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

## What's next

Aldwin runs inside your terminal process; it isn't always-on yet. Closing the terminal stops any active task, and your conversation resumes next time you run `aldwin`. It works with your own OpenAI API key only; other providers and Windows are not supported in `0.0.2`.

`0.0.3`: local models, better web search, and MCP.

---

## Uninstall

`aldwin uninstall` keeps `~/.aldwin` (your conversations, memory, and persona) and removes the command: on a `uv tool install`, it confirms (or pass `--yes`) and runs `uv tool uninstall aldwin`; otherwise it prints the pip/pipx command to run yourself.

`aldwin uninstall --purge` additionally deletes `~/.aldwin` after two confirmations (typing the folder name).

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

Further reading: the [protocol specification](https://github.com/lunarstack-co/aldwin/blob/main/docs/protocol.md), the [manual test script and release checklist](https://github.com/lunarstack-co/aldwin/blob/main/docs/manual-test.md), the [release runbook](https://github.com/lunarstack-co/aldwin/blob/main/docs/releasing.md), and the [changelog](https://github.com/lunarstack-co/aldwin/blob/main/CHANGELOG.md).

Found a bug or want to propose an improvement? Open an issue on [GitHub Issues](https://github.com/lunarstack-co/aldwin/issues).

---

## Licence

Apache-2.0. See [`LICENSE`](https://github.com/lunarstack-co/aldwin/blob/main/LICENSE) and [`NOTICE`](https://github.com/lunarstack-co/aldwin/blob/main/NOTICE).
