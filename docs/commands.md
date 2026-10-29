# Commands and shortcuts

## Terminal commands

| Command | What it does |
|---|---|
| `aldwin` | Open the interactive chat and resume the latest conversation |
| `aldwin setup` | Configure or update API keys and the default model |
| `aldwin skills` | List default and user skills from the terminal (same catalogue as `/skills`) |
| `aldwin uninstall` | Remove Aldwin (see [Uninstall](#uninstall)) |
| `aldwin --plain` | Disable ANSI colours, live animations, and the terminal bell (also enabled by `NO_COLOR=1`) |
| `aldwin --debug` | Enable `DEBUG`-level event logging in `~/.aldwin/logs/aldwin.log` |
| `aldwin --version` | Print the installed version and protocol/Agentling versions, then exit |
| `aldwin --help` | Show CLI flags and subcommands |

## Slash commands

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

## Keyboard shortcuts

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

## Uninstall

`aldwin uninstall` keeps `~/.aldwin` (your conversations, memory, and persona) and removes the command. On a `uv tool install`, it confirms (or pass `--yes`) and runs `uv tool uninstall aldwin`. Otherwise it prints the `pip` or `pipx` command to run yourself.

`aldwin uninstall --purge` also deletes `~/.aldwin`, after two confirmations (the second asks you to type the folder name).
