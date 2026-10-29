# Tools

Aldwin works through a fixed set of built-in tools. Read-only tools run without asking. Anything that changes a file or runs a command shows you what it will do first.

The approval column uses three levels:

- **No:** runs straight away.
- **Diff:** shows a coloured diff of the change and waits for `y/n`.
- **Ask:** shows the action and waits for `y/n` every time.

See [Safety](safety.md) for exactly how approvals work.

## Files

| Tool | What it does | Needs approval? |
|---|---|---|
| `list_dir` | Lists files and folders in the workspace | No |
| `glob` | Finds files by name with a glob pattern | No |
| `grep` | Searches file contents for a regular expression | No |
| `read_file` | Reads a text file and returns numbered lines | No |
| `edit_file` | Replaces a unique exact string in a file | **Diff** |
| `write_file` | Creates or overwrites a text file | **Diff** |

## Documents

| Tool | What it does | Needs approval? |
|---|---|---|
| `read_document` | Converts a PDF, Word, PowerPoint, Excel, CSV/TSV, or HTML file to markdown (`pages=` for PDF ranges, `sheet=` for an Excel sheet) | No |
| `download_file` | Downloads a public URL into the workspace | **Ask** |

Reading `.docx`, `.pptx`, and `.xlsx` needs the `aldwin[office]` extra:

```bash
uv tool install "aldwin[office]"
# or
pip install "aldwin[office]"
```

`read_document` tells you if the extra is missing. PDF, CSV/TSV, and HTML always work.

## Notes

| Tool | What it does | Needs approval? |
|---|---|---|
| `remember` | Saves a short durable fact, or updates one in place with `replace=` | No for new; **Diff** for replace |
| `recall` | Ranked search across daily notes and past conversations | No |
| `forget` | Deletes a saved note after showing it | **Diff** (never grantable) |

Notes live in `~/.aldwin/memory/notes/YYYY-MM-DD.md`. A rebuildable SQLite FTS5 index at `~/.aldwin/memory/index.sqlite` powers ranked `recall`.

## Memory and persona

- **Profile (`~/.aldwin/memory/profile.md`):** durable facts about you (up to 4,000 characters), always included in the system prompt. Only you can edit this file; the agent cannot modify it.
- **Persona (`~/.aldwin/persona.md`):** controls Aldwin's tone and style (up to 2,000 characters by default; set `limits.persona_chars` in `config.yaml` to change it). Edit the file directly, or ask Aldwin to change how it talks; it shows the proposed `persona.md` in an approval box and asks `y/n` before saving.

## Web

| Tool | What it does | Needs approval? |
|---|---|---|
| `web_search` | Searches the web via Tavily for current information | No |
| `web_fetch` | Fetches and extracts readable text from an `http`/`https` page (≤ 20,000 chars) | No |

## Terminal

| Tool | What it does | Needs approval? |
|---|---|---|
| `run_command` | Runs a shell command in the workspace, with a clean environment | **Always** |

## Planning

| Tool | What it does | Needs approval? |
|---|---|---|
| `todo` | Keeps a checklist for jobs of three or more steps, shown as a live view | No |
| `calculate` | Exact arithmetic and ISO date maths, no shell | No |

## Skills

| Tool | What it does | Needs approval? |
|---|---|---|
| `create_skill` | Drafts a new skill in `~/.aldwin/skills/` | **Diff** (never grantable) |
| `set_persona` | Updates `~/.aldwin/persona.md` | **Diff** |

See [Skills](skills.md) for how skills work and how to write your own.
