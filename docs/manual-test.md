# Aldwin 0.0.1 manual test script

This script verifies the end-to-end user journey for Aldwin `0.0.1` on clean **macOS** and **Linux** environments before release (`ENG-384` `FR-11`, `FR-12`).

---

## 1. Preparing clean environments (`OQ-1`)

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

## 2. Step-by-step verification checklist (`FR-11`)

### Step 1: Install and `--version`
- **Action:**
  - Before PyPI release: `uv tool install /dist/aldwin-*.whl` (or `uv tool install .` from checkout).
  - After PyPI release: `uv tool install aldwin`.
  - Run `aldwin --version` and `ls -la ~/.aldwin`.
- **Expected:**
  - `aldwin --version` prints `aldwin 0.0.1` (or `0.0.1.dev0` pre-release) and exits `0` in `< 0.5 s`.
  - `~/.aldwin` does **not** exist yet (`uv tool install` and `--version` have no side effects).
- [ ] Pass / Fail

### Step 2: First-run onboarding (no key, bad key, good key, skip Tavily)
- **Action:**
  1. Ensure `OPENAI_API_KEY` and `TAVILY_API_KEY` are unset.
  2. Run `aldwin`.
  3. At `OpenAI API key:`, paste an invalid key `sk-bad-key-12345678` and press Enter.
  4. Paste a valid OpenAI API key and press Enter.
  5. At `Tavily API key for web search (Enter to skip):`, press Enter.
  6. At the model prompt, press Enter to accept `gpt-4o`.
  7. In a second terminal, inspect `stat ~/.aldwin` and `stat ~/.aldwin/secrets.yaml`.
- **Expected:**
  - Welcome panel renders once (`Welcome to Aldwin`).
  - Typing/pasting the key is hidden (`••••`).
  - Bad key prints: `That key was rejected by OpenAI. Check it and paste it again.`
  - Valid key prints: `Checking key… sk-…<last4> ✓`.
  - Prints `Default model: gpt-4o (change any time with /model)` and `Created ~/.aldwin`.
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

### Step 4: Web search without a Tavily key
- **Action:** Ask `Use web_search to look up today's Python release news.`
- **Expected:**
  - Tool card shows `⚙ web_search` followed by `✗ web_search  not set up`.
  - Aldwin does not crash; it explains that web search needs a Tavily key via `aldwin setup`.
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
2. At `Server URL:` paste `https://mcp.context7.com/mcp`. Accept the suggested name `context7`.
3. Check the summary reads `Context7 has N tools: … read (run automatically), … write (ask first).`, then pick `Add Context7`.
   - Expect: `Context7 connected. N tools are available now.`
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

## 3. Manual test run log template (`FR-12`)

Copy and paste the filled-in template below as a comment on Linear ticket **ENG-384** for each platform tested (`macOS` and `Linux`):

```markdown
### Manual test run: <Platform: macOS / Linux>

- **Date:** YYYY-MM-DD
- **OS & Version:** <e.g. macOS 15.x arm64 / Ubuntu 24.04 x86_64>
- **Python Version:** <e.g. Python 3.11.x / 3.13.x>
- **Aldwin Version:** `0.0.1`

| Step | Scenario | Result (PASS/FAIL) | Notes / Linked Bug Ticket |
|---|---|---|---|
| 1 | Install & `aldwin --version` | PASS | |
| 2 | First-run onboarding (bad key, good key, skip Tavily, 0700/0600 perms) | PASS | |
| 3 | Plain question & streamed Markdown | PASS | |
| 4 | Web search without Tavily key | PASS | |
| 5 | Command approval (`n` declined, `y` approved) | PASS | |
| 6 | `Esc` mid-answer stops < 1 s with `(stopped)` | PASS | |
| 7 | Crash/close mid-task -> reopen shows interrupted -> `continue` | PASS | |
| 8 | Persona change approval (`y/n`) & persistence | PASS | |
| 9 | Slash commands (`/model`, `/memory`, `/skills`, `/tools`, `/new`, `/help`) | PASS | |
| 10 | Custom skill folder in `~/.aldwin/skills/` | PASS | |
| 11 | `Ctrl-C` once/twice & `Ctrl-D` save and exit | PASS | |
| 12 | Plain mode (`NO_COLOR=1` and `--plain`) | PASS | |
| 13 | Upgrade keeps data; `uninstall` keeps data; `--purge` deletes data | PASS | |
```

---

## 4. Release checklist (`ENG-385` `FR-8`)

Tick every item below on clean **macOS** and clean **Linux** (Python 3.11+) after installing from PyPI:

- [ ] `uv tool install aldwin` works on clean macOS and Linux (Python 3.11+)
- [ ] First `aldwin` creates the folder and asks for the key; a bad key gets a clear message
- [ ] A second `aldwin` shows and continues the previous conversation
- [ ] `"Be more direct"` updates `persona.md` after `y/n` and persists
- [ ] `/model` switches model mid-conversation
- [ ] Streaming markdown, tool cards, `Esc` stops within 1 second
- [ ] Every terminal command asks `y/n`
- [ ] A 50-turn conversation stays fast and survives tool errors
- [ ] `pytest` passes; Fola can explain every file in `src/aldwin`
