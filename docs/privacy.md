# Data and privacy

Aldwin collects no analytics or telemetry. Everything it stores lives in one folder on your computer.

## What leaves your computer

- **To OpenAI:** your prompts, the last 20 conversation exchanges, your profile and recent daily notes (within their character caps), and the contents of any file, document, or page Aldwin reads for the turn.
- **To Tavily and websites:** when `web_search` or `web_fetch` runs, search queries go to Tavily and HTTP GET requests go to the target URL.
- **To PyPI:** on launch, Aldwin may fetch public project metadata from `https://pypi.org/pypi/aldwin/json` to tell you an update is available. No user id, keys, or home path are sent. Set `check_for_updates: false` in `config.yaml` to turn this off.

Everything else under `~/.aldwin` stays on your computer, including your API keys, the approval log, the memory index, and saved conversations.

## The home folder

All local data lives in `~/.aldwin`. Set `ALDWIN_HOME=/path/to/dir` to use a different folder.

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

## Logs

- `logs/aldwin.log` records lifecycle events and errors only, never message content. Every record passes through a filter that redacts secret-shaped values (`sk-…`, `tvly-…`). Run `aldwin --debug` for more detail.
- `logs/approvals.jsonl` records every policy decision but never file contents, diffs, or tool outputs. See [Safety](safety.md#approval-log).

## Removing your data

`aldwin uninstall` keeps `~/.aldwin`. `aldwin uninstall --purge` also deletes it, after two confirmations. See [Commands](commands.md#uninstall).
