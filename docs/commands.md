# Commands and shortcuts

## Terminal commands

| Command | What it does |
|---|---|
| `aldwin` | Open the interactive chat and resume the latest conversation |
| `aldwin setup` | Configure or update API keys and the default model |
| `aldwin skills` | List default and user skills from the terminal (same catalogue as `/skills`) |
| `aldwin connectors list [--json]` | List connectors (connected apps over MCP) |
| `aldwin connectors add <name> --url <url> [--token-env VAR]` | Check an MCP server and save it as a connector |
| `aldwin connectors add <name> --command "<cmd>" [--env-allow VAR] [--cwd PATH] [--yes]` | Approve, check and save a local MCP server (macOS and Linux) |
| `aldwin connectors import [<path>] [--rename-conflicts] [--yes]` | Import MCP servers from another agent app's config file; local ones are saved disabled. Without a path, looks in the usual places of every supported app, lists what it found and asks first. See [import-sources.md](import-sources.md) |
| `aldwin connectors remove\|disable <name>` | Delete or turn off a connector |
| `aldwin connectors enable <name> [--yes]` | Turn on a connector; a local command not yet approved asks first |
| `aldwin uninstall` | Remove Aldwin (see [Uninstall](#uninstall)) |
| `aldwin --plain` | Disable ANSI colours, live animations, notifications, and the terminal bell (also enabled by `NO_COLOR=1`) |
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
| `/model [name\|all]` | Switch model or provider (local, cloud, custom servers, Add a provider, or Manage keys to update or remove a saved key) |
| `/search` | Pick a web search provider with the arrow keys: connect it (key checked before saving), use it, update its key, or disconnect it |
| `/search <name>` | Use a connected provider, or go straight to connecting it (`auto`, `searxng`, `tavily`, `brave`, `exa`, `perplexity`, `parallel`, `firecrawl`) |
| `/search status` | Print the text summary of every provider |
| `/persona` | Show the active persona and `~/.aldwin/persona.md` character count |
| `/memory` | Show profile, recent notes, and index status (`N notes · M conversations indexed · index <size>`) |
| `/skills` | List catalogue skills (`name · source · description`); `/skills show <name>` prints the body |
| `@` | Skill picker in the chat input (recent first, Tab to insert); picked skills apply to that message only |
| `/todo` | Show the current multi-step task checklist for this session |
| `/workspace` | Show the workspace folder, or switch it with `/workspace <path>` |
| `/verbose` | Show or hide up to 12 preview lines under file-tool rows for this session |
| `/notify` | Toggle terminal notifications and the bell for this session; print `notify: on` or `notify: off` |
| `/tools` | List built-in tools and which ones require approval, then each connector's tools |
| `/connectors` | Add a connector by URL or local command, import the ones set up in other agent apps, or manage one: its tools, reconnect, turn off, remove |
| `/exit` | Save the current session and exit |

Notifications start enabled for each session. Aldwin notifies when a turn takes at least
30 seconds or an approval needs input. Notification text contains only the elapsed
time or tool name, never your messages, command arguments, or file contents.

Supported terminals use OSC 9 (iTerm2, Ghostty, WezTerm, kitty) or OSC 777 (VS Code,
Konsole). VS Code needs a notification-capable terminal extension. Terminal and
desktop notification settings may also need to allow notifications. Unknown terminals
use the bell. Notifications and the bell are silent in `--plain` mode, when `NO_COLOR`
is set, and when output is redirected.

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
