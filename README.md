# Aldwin

An open-source personal assistant that works with your files and documents, knows good habits out of the box, and runs on your own computer, in your terminal, built on [Agentling](https://agentling.dev).

![Aldwin 0.0.2 session: summarising a spreadsheet and a slide deck, then finding and fixing a TODO with diff approval](https://raw.githubusercontent.com/lunarstack-co/aldwin/main/docs/assets/demo.gif)

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

The first time you run `aldwin`, it creates a private home folder at `~/.aldwin` and asks for your OpenAI API key (or uses `OPENAI_API_KEY` if it's set). A Tavily key for web search is optional; press `Enter` to skip it and add it later with `aldwin setup`.

After that, `aldwin` picks up your latest conversation where you left off. Type `/help` at the prompt to see what you can do.

---

## What Aldwin can do

- **Files:** list, find, search, and read files in your workspace; edit or create them after showing you a diff.
- **Documents:** read PDF, Word, PowerPoint, Excel, CSV, and HTML files as markdown, and download files from the web.
- **Notes and memory:** remember facts you tell it, search past notes and conversations, and keep a profile you write yourself.
- **Persona:** change how Aldwin talks by editing `persona.md` or asking it to.
- **Web:** search the web and read pages.
- **Terminal:** run shell commands, always with your approval.
- **Planning:** keep a live checklist for longer jobs and do exact arithmetic and date maths.
- **Skills:** nine built-in skills, your own skills in `~/.aldwin/skills/`, and an `@` picker to choose one for a message.
- **Connectors:** add a remote MCP server with `/connectors` and use its tools in any conversation. Tools the server marks read-only run automatically; anything else asks first.

See the [full tool list](https://github.com/lunarstack-co/aldwin/blob/main/docs/tools.md) and the [skills guide](https://github.com/lunarstack-co/aldwin/blob/main/docs/skills.md).

---

## Safety

Aldwin runs on your machine without a sandbox, so every tool call goes through a policy engine first:

- **Reading is free, changing asks.** Read-only tools run straight away. Edits show a diff and default to **No**. Shell commands and downloads ask every time.
- **Secrets are off limits.** SSH keys, cloud credentials, `.env` files and similar are blocked outright, whatever you or the model ask for.
- **Dangerous commands are refused.** `sudo`, `rm -rf /`, disk formatting and similar are refused before you're even asked.
- **Commands get a clean environment.** API keys, tokens, and passwords in your shell are never passed to commands Aldwin runs.
- **Everything read is untrusted.** Text from files, pages, and command output is treated as data, never as instructions.
- **Every decision is logged** to `~/.aldwin/logs/approvals.jsonl`, with secrets redacted.

Read the [safety model](https://github.com/lunarstack-co/aldwin/blob/main/docs/safety.md) for the full rules and known limits.

---

## Data and privacy

- **No telemetry.** Aldwin collects no analytics.
- **Sent to OpenAI:** your prompts, recent conversation, your profile and recent notes, and anything Aldwin reads for the turn.
- **Stays local:** everything else in `~/.aldwin`, including your API keys, saved conversations, and logs.

Read [data and privacy](https://github.com/lunarstack-co/aldwin/blob/main/docs/privacy.md) for the home folder layout and every network call Aldwin makes.

---

## Commands

| Command | What it does |
|---|---|
| `aldwin` | Open the chat and resume the latest conversation |
| `aldwin setup` | Set or update API keys and the default model |
| `/help` | Show slash commands and keyboard shortcuts inside the chat |
| `Esc` | Stop the current answer |

See all [commands, slash commands, and shortcuts](https://github.com/lunarstack-co/aldwin/blob/main/docs/commands.md), including how to uninstall.

---

## What's next

Aldwin runs inside your terminal process; it isn't always-on yet. Closing the terminal stops any active task, and your conversation resumes next time you run `aldwin`. It works with your own OpenAI API key only; other providers and Windows are not supported in `0.0.2`.

`0.0.3`: local models, better web search, and MCP.

---

## Contributing

Contributions are welcome. Read the [contributing guide](https://github.com/lunarstack-co/aldwin/blob/main/CONTRIBUTING.md) to get set up, and see the [docs index](https://github.com/lunarstack-co/aldwin/blob/main/docs/README.md) for the protocol, test, and release docs.

Found a bug or have an idea? Open an issue on [GitHub Issues](https://github.com/lunarstack-co/aldwin/issues).

---

## Licence

Apache-2.0. See [`LICENSE`](https://github.com/lunarstack-co/aldwin/blob/main/LICENSE) and [`NOTICE`](https://github.com/lunarstack-co/aldwin/blob/main/NOTICE).
