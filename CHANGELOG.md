# Changelog

All notable changes to Aldwin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

- Wheel: ~0.23 MB. Installed (no extras): ~116 MB on macOS arm64, within the 120 MB budget (Linux x86_64 to be confirmed before publish). With `aldwin[office]`: ~131 MB.

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

[0.0.2]: https://github.com/lunarstack-co/aldwin/releases/tag/v0.0.2
[0.0.1]: https://github.com/lunarstack-co/aldwin/releases/tag/v0.0.1
