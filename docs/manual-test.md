# Aldwin manual test script

This script verifies the end-to-end user journey on clean **macOS** and **Linux** environments before each release.

---

## 1. Preparing clean environments

### Option A: Clean Linux environment (Docker or GitHub Codespace)

```bash
# From the repository root (after building `dist/*.whl` with `uv build`):
docker run --rm -it -v "$PWD/dist:/dist:ro" python:3.11-slim bash

# Inside the container:
apt-get update && apt-get install -y curl procps
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
unset OPENAI_API_KEY TAVILY_API_KEY ALDWIN_HOME NO_COLOR
```

### Option B: Clean macOS environment (Tart VM, clean user account, or isolated `$HOME`)

```bash
# Either inside a fresh Tart macOS VM / clean user account, or an isolated shell:
mkdir -p /tmp/aldwin-clean-mac/home /tmp/aldwin-clean-mac/tools /tmp/aldwin-clean-mac/bin
export HOME="/tmp/aldwin-clean-mac/home"
export UV_TOOL_DIR="/tmp/aldwin-clean-mac/tools"
export UV_TOOL_BIN_DIR="/tmp/aldwin-clean-mac/bin"
export PATH="$UV_TOOL_BIN_DIR:$PATH"
unset OPENAI_API_KEY TAVILY_API_KEY ALDWIN_HOME NO_COLOR
```

---

## 2. Step-by-step verification checklist

### Step 1: Install and `--version`
- **Action:**
  - Before PyPI release: `uv tool install /dist/aldwin-*.whl` (or `uv tool install .` from checkout).
  - After PyPI release: `uv tool install aldwin`.
  - Run `aldwin --version` and `ls -la ~/.aldwin`.
- **Expected:**
  - `aldwin --version` prints the installed version and exits `0` in `< 0.5 s`.
  - `~/.aldwin` does **not** exist yet (`uv tool install` and `--version` have no side effects).
- [ ] Pass / Fail

### Step 2: First-run onboarding (cloud path; no key, bad key, good key, skip Tavily)

For the local-first path (Ollama/LM Studio found on first run), see **Step 20**. This step covers the cloud path when no local model is running.

- **Action:**
  1. Ensure `OPENAI_API_KEY` and `TAVILY_API_KEY` are unset, and that Ollama/LM Studio are not serving models (or quit them).
  2. Run `aldwin`.
  3. Choose a cloud provider (for example OpenAI) when offered.
  4. At the API key prompt, paste an invalid key `sk-bad-key-12345678` and press Enter.
  5. Paste a valid API key and press Enter.
  6. At `Tavily API key for web search (Enter to skip):`, press Enter.
  7. At the model prompt, press Enter to accept the default.
  8. In a second terminal, inspect `stat ~/.aldwin` and `stat ~/.aldwin/secrets.yaml`.
- **Expected:**
  - Welcome panel renders once (`Welcome to Aldwin`).
  - Typing/pasting the key is hidden (`••••`).
  - Bad key prints a clear rejection naming the provider (for example `That key was rejected by OpenAI. Check it and paste it again.`).
  - Valid key prints `Checking key…` with a masked key and `✓`.
  - Prints a default model line mentioning `/model` and `Created ~/.aldwin`.
  - `~/.aldwin` has mode `0700`; `~/.aldwin/secrets.yaml` has mode `0600`.
  - Drops directly into the `❯ ` chat prompt (`> ` with `--plain`) without printing a second welcome box.
- [ ] Pass / Fail

### Step 3: Plain question and streamed Markdown answer
- **Action:** Ask `What can you do? Show a short Python code block.`
- **Expected:**
  - Spinner shows `Thinking…` immediately after pressing Enter.
  - Markdown answer streams cleanly into scrollback with syntax-highlighted Python code.
  - Turn ends with `✻ Worked for <N>s`; when tools ran it includes `· <N> tool(s)`, and when token usage is available it includes `· <N> tokens`. Plain mode uses `*` instead of `✻`.
- [ ] Pass / Fail

### Step 4: Web search with nothing connected
- **Action:** With no search key and no SearXNG running, ask `Use web_search to look up today's Python release news.`
- **Expected:**
  - Tool card shows `⚙ web_search` followed by `✗ web_search  not set up`.
  - Aldwin does not crash; it tells you to run `/search` to connect a search provider.
- [ ] Pass / Fail

### Step 5: Command approval (`no` then `yes`)
- **Action:**
  1. Ask `How much free disk space do I have? Run df -h.`
  2. When the `Run this command?` approval panel appears, press `n`.
  3. Ask `Please check free disk space with df -h again.`
  4. When the approval panel appears, press `y`.
- **Expected:**
  - Terminal rings the bell (`\a`) and displays the bordered `Run this command?` panel with `df -h  [y] yes  [N] no`.
  - Pressing `n` prints `✗ declined  df -h` and `✗ run_command  declined`; command does **not** run; Aldwin acknowledges the decline.
  - Pressing `y` prints `✓ approved  df -h` and `✓ run_command  <duration> · exit 0`, and Aldwin reports the free disk space.
- [ ] Pass / Fail

### Step 6: `Esc` mid-answer stops within 1 second
- **Action:** Ask `Write a detailed 600-word guide to Unix signals.` and press `Esc` while text is streaming.
- **Expected:**
  - Streaming stops in `< 1 s`.
  - Partial text remains visible in scrollback, followed by `⎿ Interrupted · tell Aldwin what to do instead` (plain mode: `| Interrupted · tell Aldwin what to do instead`).
  - Prompt `❯ ` (or `> ` with `--plain`) returns immediately.
- [ ] Pass / Fail

### Step 7: Close terminal mid-task, then resume with `continue`
- **Action:**
  1. Ask `Write a very long history of computing.`
  2. While Aldwin is streaming (or waiting on a tool), close the terminal window or run `kill -9 <aldwin_pid>` from another shell.
  3. Run `aldwin` again.
  4. Type `continue` and press Enter.
- **Expected:**
  - Reopening `aldwin` displays the previous messages dimmed, the `──── continuing from … ────` divider, and the warning: `Your last request was interrupted. Say continue to pick it up.`
  - Typing `continue` resumes the conversation cleanly.
- [ ] Pass / Fail

### Step 8: Persona change requires `y/n` and persists across sessions
- **Action:**
  1. Say `Be more direct and concise: update my persona so you always answer in at most two bullet points.`
  2. Approve the `Update persona?` panel with `y`.
  3. Exit with `/exit`, reopen `aldwin`, run `/persona`, and ask `Explain what TCP is.`
- **Expected:**
  - `set_persona` shows the `Update persona?` approval panel before touching `persona.md`.
  - After pressing `y`, `~/.aldwin/persona.md` is updated.
  - In the new session, `/persona` shows the updated text and Aldwin answers in at most two bullet points.
- [ ] Pass / Fail

### Step 9: Slash commands (`/model`, `/memory`, `/skills`, `/tools`, `/new`, `/help`)
- **Action:**
  1. Type `/` and verify the completion menu lists `/help`, `/new`, `/model`, `/persona`, `/memory`, `/skills`, `/tools`, `/exit`.
  2. Run `/help`.
  3. Run `/model` and select another model (e.g. `gpt-4.1` or `gpt-4o-mini`).
  4. Ask `Remember that my favourite editor is Neovim.` then run `/memory`.
  5. Run `/skills` and `/tools`.
  6. Run `/new`.
  7. Run `/unknown`.
- **Expected:**
  - `/help` lists all slash commands and keyboard shortcuts (`Enter`, `Alt+Enter`, `Esc`, `Ctrl-C`, `Ctrl-D`).
  - `/model` shows canonical models (hiding dated snapshots unless `/model all`), switches model, updates the bottom toolbar, and saves to `config.yaml`.
  - `/memory` shows `~/.aldwin/memory/profile.md` size and the recent note (`favourite editor is Neovim`).
  - `/skills` lists `using-aldwin`.
  - `/tools` lists `web_search`, `web_fetch`, `run_command`, `remember`, `recall` with approval notes.
  - `/new` starts a fresh conversation while keeping the previous `.json` file in `~/.aldwin/sessions/`.
  - `/unknown` prints `Unknown command. Type /help.` without calling the model.
- [ ] Pass / Fail

### Step 10: Add a custom skill folder
- **Action:**
  1. Exit Aldwin and create `~/.aldwin/skills/pirate-mode/SKILL.md`:
     ```markdown
     ---
     name: pirate-mode
     description: Speak like a 17th-century sea captain when asked.
     ---
     When the user asks for pirate mode, reply with nautical metaphors.
     ```
  2. Reopen `aldwin`, run `/skills`, and ask `Use the pirate-mode skill to greet me.`
- **Expected:**
  - `/skills` lists both `using-aldwin` and `pirate-mode`.
  - Aldwin loads `pirate-mode` (`✓ load_skill  <duration> · loaded`) and replies in nautical style.
- [ ] Pass / Fail

### Step 11: `Ctrl-C` once/twice and `Ctrl-D`
- **Action:**
  1. At an empty `❯ ` prompt (or `> ` with `--plain`), press `Ctrl-C` once.
  2. Press `Ctrl-C` a second time within 2 seconds.
  3. Reopen `aldwin` and press `Ctrl-D` at an empty prompt.
- **Expected:**
  - First `Ctrl-C` prints `Press Ctrl-C again to quit.` and stays in the chat.
  - Second `Ctrl-C` saves the session, prints `Saved. See you.`, and exits `0`.
  - `Ctrl-D` saves the session, prints `Saved. See you.`, and exits `0`.
- [ ] Pass / Fail

### Step 12: Plain mode (`NO_COLOR=1` and `--plain`)
- **Action:**
  1. Run `NO_COLOR=1 aldwin` and ask `Say hi in one sentence.` then `/exit`.
  2. Run `aldwin --plain` and ask `Say hi in one sentence.` then `/exit`.
- **Expected:**
  - Output has no ANSI colour codes, no animated spinner frames, and no terminal bell, while keeping the same readable layout.
- [ ] Pass / Fail

### Step 13: Upgrade, `aldwin uninstall`, and `aldwin uninstall --purge`
- **Action:**
  1. Run `uv tool upgrade aldwin` (or reinstall with `uv tool install --force ...`) and verify `~/.aldwin/persona.md`, `memory/`, and `sessions/` are untouched.
  2. Run `aldwin uninstall`, answer `y` when asked to run `uv tool uninstall aldwin` (or use `--yes`).
  3. Run `aldwin uninstall --purge`, answer `n` first (verify data kept), then run `aldwin uninstall --purge`, answer `y`, and type the folder name (`home` or `.aldwin`).
- **Expected:**
  - `uv tool upgrade` keeps all user data and custom edits intact.
  - `aldwin uninstall` keeps `~/.aldwin`, runs `uv tool uninstall aldwin` after confirm, and ends with `Aldwin removed. Your data is still in …`.
  - `aldwin uninstall --purge` asks twice (`[y/n]` + exact folder name), deletes `~/.aldwin`, then uninstalls or prints the tool-remove step as above.
- [ ] Pass / Fail

---

### Step 14: Connectors (MCP): Context7 without auth, GitHub with a token

Needs network access and, for GitHub, a token in `GITHUB_TOKEN` (a fine-grained token with read access to one repository is enough).

1. In a chat, type `/connectors`. The list shows only `+ Add a connector…`. Pick it.
2. The catalogue lists its apps under headings (`Work and projects` first, `Search and knowledge` last), with `Notion` first and `Custom URL…` last. Headings have no number and can't be picked. Browser sign-in apps are tagged `[sign in]`, GitHub `[token]`, and `Context7`, `Exa` and `Wolfram` `[no sign-in]`. Pick `Context7`.
3. Expect: `Context7 connected. N tools are available now.` (To try the probe summary, pick `Custom URL…` instead and paste `https://mcp.context7.com/mcp`.)
4. Ask: "Using Context7, find the docs for next.js routing."
   - Expect: a tool row `⚙ Context7 · resolve-library-id …  auto · server says read-only` and no approval prompt.
5. In a shell: `aldwin connectors add github --url https://api.githubcopilot.com/mcp/ --token-env GITHUB_TOKEN`.
   - Expect: `Github added: …` and exit code `0` (`echo $?`).
6. Open a **second** terminal and run `aldwin`. Type `/connectors`.
   - Expect: both `Context7` and `Github` listed as `●` connected.
7. Ask something that makes a GitHub write call (for example "create an issue titled test in <repo>").
   - Expect: an approval panel `Github wants to run <tool>`, labelled `write · external`, with `No` selected and the choices `No`, `Yes`, `Yes for this session`. Answer `No`.
8. `/connectors → Github → Tools`: set a write tool to `off`.
   - Expect: Aldwin no longer offers that tool (ask it to use the tool; it says it can't).
9. Turn the network off for a minute.
   - Expect: a dim line `Github disconnected; retrying.` and, after the network returns, `Github reconnected.`
10. `/connectors → Github → Remove → Yes`.
    - Expect: `aldwin connectors list --json` shows only `context7`, and `~/.aldwin/credentials/mcp/` has no `github.json`.
11. Check that no token was written anywhere else: `grep -r "$GITHUB_TOKEN" ~/.aldwin` prints nothing.

### Step 15: Connectors: sign in with your browser

Needs network access and a Linear and a Notion account (free plans work).

1. `/connectors → + Add a connector… → Linear`.
   - Expect: `Sign in to Linear. This link works for 5 minutes:`, the address on the next line, and your browser opening it. Under it, `Paste the address, or press Enter to cancel:`.
2. Approve in the browser.
   - Expect: the tab says `You're signed in. You can close this tab.` and the terminal prints `Linear connected. N tools are available now.`
3. `ls -l ~/.aldwin/credentials/mcp/linear.json`.
   - Expect: `-rw-------`.
4. Ask: "Using Linear, list my teams."
   - Expect: a `Linear` tool row and an answer.
5. `/connectors → + Add a connector… → Notion`, then press Enter at the paste prompt without using the browser.
   - Expect: `Sign-in cancelled.` and `Run /connectors to try again.`; `/connectors` does not list Notion.
6. In a shell: `aldwin connectors login linear --paste`. Open the printed address, approve, copy the address the browser ends on (it starts `http://127.0.0.1:47823/callback`) and paste it.
   - Expect: `Linear signed in: N tools.` Pasting something else first prints `That address has no sign-in code. Paste the full address your browser ended on.` and asks again.
7. `/connectors → Linear → Sign out`.
   - Expect: `Linear signed out. Sign in again from /connectors.` The list shows `! Linear  needs sign-in`. Ask a Linear question: Aldwin says you need to sign in again with /connectors, and no browser opens.
8. Quit and start `aldwin` again, then send any message.
   - Expect: one dim line `Linear needs you to sign in again: /connectors.` on the first turn only.
9. `/connectors → Linear → Sign in`, approve in the browser.
   - Expect: `Linear connected.`
10. `/connectors → + Add a connector… → GitHub` (remove it first if Step 14 left it).
    - Expect: `GitHub needs a token.`, `Create one at https://github.com/settings/personal-access-tokens/new` and a hidden `Token (hidden):` prompt. Paste `$GITHUB_TOKEN`'s value; expect `GitHub connected.`
11. Over SSH (or with `SSH_CONNECTION=x aldwin`), add Notion.
    - Expect: no browser opens; the address is printed with `Open the address above in a browser and sign in, then paste the address the browser ends on.`
12. Check that no token or code was written outside the credentials folder: `grep -rlE "access_token|refresh_token|code=" ~/.aldwin --exclude-dir=credentials` prints nothing.
13. With Linear and Notion both connected, ask a Linear question, then a Notion question, then another Linear question.
    - Expect: `/connectors` shows both `● connected` with no warning. The first Linear answer and the first Notion answer each start with a `find_connector_tools` tool row, then the app's own tool row. The second Linear question has no `find_connector_tools` row. After `/new`, the next Linear question shows the `find_connector_tools` row again.

### Step 16: Connectors: local servers

Needs Node.js with `npx` (macOS or Linux; Windows is not supported).

1. `/connectors → + Add a connector… → Local command… (advanced)`.
   - Expect: the note `This runs a program on this computer with access to your workspace folder.` and a `Command:` prompt.
2. Enter `npx -y @modelcontextprotocol/server-everything`, then press Enter at `Name [everything]`.
   - Expect: an approval panel with `Run a local connector: everything`, the labels `runs a program on this computer` and `package version not pinned`, and `Command:`, `Folder:` (your workspace) and `Environment:` lines with variable names only.
3. Press Enter.
   - Expect: `Everything was not approved, so nothing was saved.` `/connectors` does not list it.
4. Repeat steps 1 and 2 and press `y`.
   - Expect: `Everything connected.` and a tool count. `~/.aldwin/connectors.yaml` has `command: npx` and an `approved_digest`.
5. Ask: "Use the everything server to echo hello."
   - Expect: a `find_connector_tools` row, then an `Everything` tool row that asks first, and the answer.
6. In another terminal: `pgrep -f server-everything` and note the pid (P), then `/exit` in Aldwin.
   - Expect: within 5 seconds `pgrep -g P` prints nothing.
7. `ls -l ~/.aldwin/logs/mcp-everything.log`.
   - Expect: `-rw-------`. Its contents never appeared in the chat.
8. In `connectors.yaml`, change one of the `args` by hand, then start `aldwin` and run `/connectors`.
   - Expect: `✕ everything  failed: needs approval: open /connectors to enable it`, and no new `server-everything` process. Disable and enable it to approve again.
9. In a shell: `aldwin connectors add pyver --command "python3 -V"` with stdin not a terminal (for example `< /dev/null`).
   - Expect: `Adding a local command needs approval; rerun with --yes.` and exit code 1.

### Step 17: Connectors: import

Use a real MCP config file from another editor or desktop app (a JSON file with an `mcpServers` or `servers` key; see `docs/commands.md` for the apps supported). Copy it to a scratch folder first.

1. `aldwin connectors import <copy>`, then `echo $?`.
   - Expect: a table with `name`, `type` and `status` columns, then `Imported N connectors. M values moved to Aldwin's credential store.` (`1 connector` or `1 value` when the count is 1), and `0`.
2. For each secret value in the source file, `grep -c '<value>' ~/.aldwin/connectors.yaml`.
   - Expect: `0` every time.
3. Run the same import again.
   - Expect: every row reads `skipped: name already exists` and the exit code is 1.
4. Run `uv run aldwin connectors import` with no path, in a fresh home (`ALDWIN_HOME=$(mktemp -d)`).
   - Expect: `Found:` and one line per config file found (app, scope, server count, path), then `Import these? [y/N]`. Enter prints `Nothing imported.`; `y` imports and the table gains a `from` column when more than one file was found. A file that can't be read shows `can't read: …` on its line and a `warning:` on stderr, and the others still import.
5. In a fresh home, start `aldwin`, open `/connectors` and pick `Import from other apps…`.
   - Expect: the same `Found:` lines, `Import these? [y/N]`, then one line per server and the summary. The list now shows the imported entries.
6. Start `aldwin` and open `/connectors`.
   - Expect: imported local servers show `disabled · review and enable`. Enable one: the approval panel appears, and after `y` it connects and the note is gone.

### Step 18: Ask Aldwin to connect

Needs a real model and an account with one catalogue app, for example Linear.

1. Start `aldwin` in a fresh home (`ALDWIN_HOME=$(mktemp -d)`) and ask: "Connect me to Linear and list my open issues."
   - Expect: an approval panel reading `Aldwin wants to add the Linear connector`, then `https://mcp.linear.app/mcp · sign in with your browser`, with the label `adds an external connector` and no "yes for this session" option.
2. Press `y` and finish the sign-in in the browser.
   - Expect: `Linear connected.` and, in the same reply, your open issues.
3. Ask the same again.
   - Expect: no panel; Aldwin says Linear is already connected and answers.
4. Ask: "Connect to https://mcp.example.com/mcp", then answer `n`.
   - Expect: the panel shows the URL, `custom server, not in the catalogue` and `Its tools will ask before running.`; after `n`, Aldwin says it didn't add it and nothing new appears in `/connectors`.
5. Ask Aldwin to connect GitHub.
   - Expect: a hidden `Token (hidden):` prompt, not a request to paste the token into the chat. Press Enter: the turn stops and nothing is saved.

### Step 19: Pasted secrets

1. In the chat, type `connect me to GitHub, my token is ghp_` followed by 36 letters and press Enter.
   - Expect: `That looks like a secret. I removed it before sending; use /connectors or the private prompt.` on its own line before the reply. Aldwin still answers the rest of the message (it proposes the GitHub connector, whose hidden prompt asks for a token).
2. After the reply, in another terminal:
   - `grep -c ghp_ ~/.aldwin/history` prints `0`.
   - `grep -c ghp_ ~/.aldwin/sessions/*.json` prints `0` for every file.
   - `grep -c redacted ~/.aldwin/sessions/*.json` prints at least `1` for the newest file (the marker is stored as `\u2039redacted\u203a`).
3. Press up-arrow: the pasted line is still there for this session.

### Step 20: Providers

Needs an OpenAI API key and Ollama running locally with a tool-capable model (e.g. `qwen3:8b`).

1. Start `aldwin` and verify status bar:
   - Expect: bottom status bar shows `{model id} · OpenAI` (e.g. `gpt-4o · OpenAI`).
2. Run `/model`:
   - Expect: "Available providers" menu with Ollama, LM Studio, OpenAI, followed by `Add a provider…` and `Other server`.
3. Pick `Ollama`:
   - Expect: "Ollama models" sub-menu opens with Ollama models (`✓ tools · 32K`).
   - Press `Esc`: returns back to "Available providers".
   - Pick `Ollama` again, then select `qwen3:8b`:
   - Expect: `✓ Switched to qwen3:8b (Ollama, local).` printed; status bar updates to `qwen3:8b · Ollama`.
   - Ask a calculation: `Calculate 24 * 15`. Expect: `calculate` tool executes and returns `360`.
4. Ollama outage & recovery (no-flicker test):
   - Stop Ollama (`pkill ollama` or quit app).
   - Run `/model`, select `Ollama`. Expect: retained models still listed with indicator `Ollama · not running at http://localhost:11434`.
   - Start Ollama again. Run `/model`, select `Ollama`. Expect: Ollama status recovers to active without restart.
5. Add a provider:
   - Run `/model`, select `Add a provider…`, select `DeepSeek`.
   - Expect: masked key prompt `DeepSeek API key:`. Key input does not echo.
   - Enter invalid key: error shown, secrets file untouched.
   - Repeat with `OpenRouter` and the fake key `sk-or-v1-notarealkey000`. Expect: `Your OpenRouter key was rejected. Check the key and try again.`
5a. Manage keys:
   - Run `/model`, select `Manage keys…`, pick a provider that is not running the current model, then `Disconnect` and `y`.
   - Expect: `✓ Disconnected <Provider>.`; the provider leaves "Available providers" and its key leaves `secrets.yaml`.
   - Try to disconnect the provider of the current model. Expect: `<Provider> runs the current model. Switch to another model first.`
6. Typed `/model`:
   - Type `/model gpt-4o`.
   - Expect: switches directly without picker, prints `✓ Switched to gpt-4o (OpenAI).`, status bar updates.

### Step 21: Connect web search

Needs at least one real search key (any of Tavily, Brave, Exa, Perplexity, Parallel, Firecrawl). Never paste a real key into a terminal you are screen-sharing.

1. Run `/search`.
   - Expect: `Web search · …` header, a `Connected` group, a `Not connected` group, and `Automatic` last. Brave shows `key check uses one Brave query`; Tavily shows `key check can take up to 20 seconds` until it is connected.
2. Pick a provider you have no key for and paste `fake-key-000000`.
   - Expect: `Checking key…`, then `<Provider> rejected this key (HTTP 401). Check it and try again.` (Brave says `HTTP 422`), and the key prompt again. Press `Enter`: `Cancelled.` Nothing is written to `secrets.yaml`.
3. Pick a provider you have a real key for and paste it.
   - Expect: `✓ <Provider> connected · now used for search`. The status bar shows the provider. Ask `Use web_search to look up today's Python release news.` and get numbered results.
4. Run `/search`, pick the connected provider, then `Disconnect` and `y`.
   - Expect: `✓ <Provider> disconnected`; the list falls back to `Automatic`, and the key is gone from `secrets.yaml`.
5. Set `export BRAVE_API_KEY=anything`, start `aldwin`, run `/search`, pick Brave, `Disconnect`, `y`.
   - Expect: `Brave's key comes from BRAVE_API_KEY in your environment. Remove it from your shell profile, then restart Aldwin.`
6. Pick SearXNG and press `Enter` at the address prompt.
   - Expect: `Checking SearXNG…`, then either `✓ SearXNG connected at http://127.0.0.1:8080 · now used for search` or `Could not reach SearXNG (no SearXNG with JSON at http://127.0.0.1:8080). …`.
7. Run `/search status`.
   - Expect: the plain text list of every provider with `ready` / `not ready`.
- [ ] Pass / Fail

### Step 22: The browser (`aldwin web`)

Use a home folder that has a working model. Keep the terminal that runs `aldwin web` visible.

1. Run `aldwin web`.
   - Expect: `Aldwin web is running at http://127.0.0.1:7841`, `Open this link to start: http://127.0.0.1:7841/?t=…`, `Press Ctrl-C to stop.`, and the browser opening on a new chat. The `?t=…` part is gone from the address bar.
2. Open `http://127.0.0.1:7841/` in a private window, without the link.
   - Expect: ``Open Aldwin from the link that `aldwin web` printed.`` and no chat.
3. In a second terminal, run `aldwin web`.
   - Expect: `aldwin web is already running at http://127.0.0.1:7841 (pid …). Stop it with Ctrl-C in its terminal first.` and exit code 1.
4. Run `aldwin web --host 0.0.0.0`.
   - Expect: `aldwin web only listens on this computer. Use --host 127.0.0.1, localhost or ::1.`
5. Send `Explain what a workspace is in two sentences.`
   - Expect: the answer streams in; **Stop** shows while it runs; the conversation appears under **Recents** with a title.
6. Ask `Make a file called web-test.md with one line: hello.`
   - Expect: an approval card titled `Write a file` with the new file as a diff, the message box locked, and `Aldwin needs your OK`. Pressing `Esc` does nothing. **Deny** with a reason leaves no file. Ask again and **Allow**: the file exists in the workspace.
7. Switch the view menu from **Clean** to **Detail** and ask something that searches or reads a file.
   - Expect: Clean shows one `Worked for …` line; Detail shows each step.
8. From the conversation's options menu: **Rename**, **Pin chat**, then **Delete** and confirm.
   - Expect: the new title shows; a **Pinned** group appears and goes when unpinned; after delete the chat is gone, a new chat opens, and its file is gone from `~/.aldwin/sessions/`.
9. Open **Settings**. Under **Models**, add a key with a wrong value, then a real one. Under **Search** and **Tools**, connect one provider and one app (Context7 needs no sign-in).
   - Expect: the wrong key is refused with the reason and nothing saved; the real key shows masked; the app shows `Connected` with its tool count.
10. Open `http://127.0.0.1:7841/` in a second tab of the same browser.
    - Expect: `Open in another tab` with **Take over**; taking over disconnects the first tab.
11. In a terminal, run `aldwin` on the same home folder.
    - Expect: the conversation from the browser is there, with the same model.
12. Press `Ctrl-C` in the `aldwin web` terminal.
    - Expect: the page shows `Connection lost. Reconnecting…`, then `Aldwin stopped` with **Reload**.
- [ ] Pass / Fail

---

## 3. Manual test run log template

Copy the template below and fill it in for each platform tested (`macOS` and `Linux`):

```markdown
### Manual test run: <Platform: macOS / Linux>

- **Date:** YYYY-MM-DD
- **OS & Version:** <e.g. macOS 15.x arm64 / Ubuntu 24.04 x86_64>
- **Python Version:** <e.g. Python 3.11.x / 3.13.x>
- **Aldwin Version:** `<version>`

| Step | Scenario | Result (PASS/FAIL) | Notes / Linked Bug Ticket |
|---|---|---|---|
| 1 | Install & `aldwin --version` | PASS | |
| 2 | First-run onboarding (bad key, good key, skip Tavily, 0700/0600 perms) | PASS | |
| 3 | Plain question & streamed Markdown | PASS | |
| 4 | Web search with nothing connected | PASS | |
| 5 | Command approval (`n` declined, `y` approved) | PASS | |
| 6 | `Esc` mid-answer stops < 1 s with `(stopped)` | PASS | |
| 7 | Crash/close mid-task -> reopen shows interrupted -> `continue` | PASS | |
| 8 | Persona change approval (`y/n`) & persistence | PASS | |
| 9 | Slash commands (`/model`, `/memory`, `/skills`, `/tools`, `/new`, `/help`) | PASS | |
| 10 | Custom skill folder in `~/.aldwin/skills/` | PASS | |
| 11 | `Ctrl-C` once/twice & `Ctrl-D` save and exit | PASS | |
| 12 | Plain mode (`NO_COLOR=1` and `--plain`) | PASS | |
| 13 | Upgrade keeps data; `uninstall` keeps data; `--purge` deletes data | PASS | |
| 14 | Connectors: Context7 without auth, GitHub with a token | PASS | |
| 15 | Connectors: sign in with your browser | PASS | |
| 16 | Connectors: local servers | PASS | |
| 17 | Connectors: import | PASS | |
| 18 | Ask Aldwin to connect | PASS | |
| 19 | Pasted secrets | PASS | |
| 20 | Providers | PASS | |
| 21 | Connect web search | PASS | |
| 22 | The browser (`aldwin web`): link, approval card, conversations, settings, second tab, stop | PASS | |
```

---

## 4. Release checklist

Tick every item below on clean **macOS** and clean **Linux** (Python 3.11+) after installing from PyPI:

- [ ] `uv tool install aldwin` works on clean macOS and Linux (Python 3.11+)
- [ ] First `aldwin` creates the folder and asks for the key; a bad key gets a clear message
- [ ] A second `aldwin` shows and continues the previous conversation
- [ ] `"Be more direct"` updates `persona.md` after `y/n` and persists
- [ ] `/model` switches model mid-conversation
- [ ] `/search` connects a provider; `web_search` works for SearXNG and one hosted key
- [ ] `aldwin web` opens the browser, serves the real UI (not the placeholder page), and a turn with an approval works end to end
- [ ] Streaming markdown, tool cards, `Esc` stops within 1 second
- [ ] Every terminal command asks `y/n`
- [ ] A 50-turn conversation stays fast and survives tool errors
- [ ] `pytest` passes; Fola can explain every file in `src/aldwin`
